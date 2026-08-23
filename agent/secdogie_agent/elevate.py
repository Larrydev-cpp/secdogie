"""Run a specific command as SYSTEM, on the visible desktop.

Some tasks genuinely need SYSTEM (install an MSI, edit a protected system file,
manage a service, write under HKLM) -- more than an ordinary admin can do. This
lets an *already-elevated* agent hand one command (or one GUI app) to SYSTEM and
have it appear on the desktop you can see, so the vision loop can then drive it
with the normal --window / focus / virtual-desktop machinery.

## Why it works the way it does

You cannot just "run the whole agent as SYSTEM": since Vista, services and
SCM-spawned SYSTEM processes live in **session 0**, which has no desktop the user
sees, so a screenshot-and-click agent there sees nothing useful. The working
pattern -- the one PsExec's `-s -i` uses -- is to take a SYSTEM token and
`CreateProcessAsUser` it **into the current interactive session**
(`winsta0\\default`), where its window is on the visible desktop.

## The honest boundary

Everything here is documented Win32 (`WTSGetActiveConsoleSessionId`,
`OpenProcessToken` + `DuplicateTokenEx` off winlogon.exe, `CreateProcessAsUser`)
and it **requires the caller to already be an Administrator**. It is not a UAC
bypass and exploits nothing -- it's the standard administrative technique for
acquiring SYSTEM *from* an admin context, the same thing Sysinternals PsExec
does. Point it only at machines you own and administer.

Windows-only. Off Windows, and when not elevated, every entry point degrades to
a clean, honest refusal -- it never pretends, and never tries to work around the
missing privilege. The token dance is on-machine; the *decision* of whether a
launch may proceed is a pure function, unit-tested here.

NT SERVICE\\TrustedInstaller impersonation and anti-EDR tricks are not in this
module and will not be added. They are documented refusals
(`try_impersonate_trusted_installer`, `try_edr_evasion`) so a caller that asks
for them gets an honest no instead of a silent no-op or a malware-shaped
workaround. Atlas (`atlas.py` / `native/atlas`) is the same wall.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

# Outcome labels (what callers log / branch on), osfocus-style.
LAUNCHED = "launched"          # the process was created as SYSTEM in the session
NOT_ELEVATED = "not-elevated"  # we aren't admin -- required, and we won't fake it
NO_SESSION = "no-session"      # no active interactive session to launch into
UNSUPPORTED = "unsupported"    # not Windows / a Win32 call is unavailable
BAD_COMMAND = "bad-command"    # empty/whitespace command
FAILED = "failed"             # the token dance itself failed
REFUSED_IDENTITY = "refused-identity"  # TI impersonation / anti-EDR -- never implemented


@dataclass(frozen=True)
class LaunchDecision:
    """The pure verdict: may this launch proceed, and if not, why."""

    ok: bool
    reason: str  # one of the labels above; "proceed" when ok


@dataclass(frozen=True)
class ElevateResult:
    """The outcome of an actual launch attempt."""

    outcome: str  # one of the labels above
    pid: int | None = None      # the SYSTEM process's PID, so it can be driven/tracked
    detail: str = ""


# -- operator allowlist (the safety boundary for the model) --------------------


def normalize_command(command: str | None) -> str:
    """Canonical form for comparing a command against the allowlist: trimmed,
    with internal whitespace runs collapsed to one space. Case-sensitive -- a
    command's arguments can be, and the operator declares the exact string."""
    if not command:
        return ""
    return " ".join(command.split())


def is_permitted(command: str | None, allowlist) -> bool:
    """Whether `command` is one the operator explicitly allowed to run elevated.

    This is the real gate on what the *model* can escalate: elevation is confined
    to commands a human declared at launch (see cli --allow-elevated-command), so
    the model picks from a pre-approved set, never an arbitrary SYSTEM shell. An
    empty allowlist permits nothing (elevation is off). Exact match on the
    normalized string -- no globbing or prefix matching, so "sc stop X" can never
    stand in for "sc stop Y".
    """
    if not allowlist:
        return False
    target = normalize_command(command)
    if not target:
        return False
    return any(normalize_command(a) == target for a in allowlist)


# -- pure decision core (provable without Windows) -----------------------------


def plan_launch(command: str | None, *, elevated: bool, session_id: int | None, on_windows: bool) -> LaunchDecision:
    """Decide whether a SYSTEM launch may proceed, and name the reason if not.

    Pure and fully injectable, so every refusal path is unit-tested with no
    Windows. The order matters: report the *most fundamental* blocker first
    (wrong OS, then missing privilege, then a bad request), so the message a
    caller shows points at what actually needs fixing.
    """
    if not on_windows:
        return LaunchDecision(False, UNSUPPORTED)
    if not elevated:
        # Required, and deliberately not worked around: acquiring SYSTEM needs an
        # admin token to start from. If we aren't admin, the honest answer is
        # "run me elevated first", not an attempt to escalate.
        return LaunchDecision(False, NOT_ELEVATED)
    if not command or not command.strip():
        return LaunchDecision(False, BAD_COMMAND)
    if session_id is None:
        return LaunchDecision(False, NO_SESSION)
    return LaunchDecision(True, "proceed")


# -- detection (on-machine; clean off Windows) ---------------------------------


def is_elevated() -> bool:
    """True if this process is running elevated (Administrator or SYSTEM). False
    off Windows, or if it can't be determined -- fail toward "not elevated" so
    the caller refuses rather than attempting a launch that will only fail."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def current_integrity() -> str:
    """A human-readable integrity level for status/error messages:
    "system"|"high"|"medium"|"low"|"unknown". Best-effort; never raises."""
    if not sys.platform.startswith("win"):
        return "unknown"
    try:
        import ctypes
        from ctypes import wintypes

        # Read the token's integrity level SID; its RID maps to a level.
        TokenIntegrityLevel = 25
        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32

        token = wintypes.HANDLE()
        TOKEN_QUERY = 0x0008
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            return "unknown"
        try:
            size = wintypes.DWORD(0)
            advapi32.GetTokenInformation(token, TokenIntegrityLevel, None, 0, ctypes.byref(size))
            buf = (ctypes.c_byte * size.value)()
            if not advapi32.GetTokenInformation(token, TokenIntegrityLevel, buf, size, ctypes.byref(size)):
                return "unknown"
            # buf is a TOKEN_MANDATORY_LABEL: SID_AND_ATTRIBUTES { PSID, DWORD }.
            psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
            count_ptr = advapi32.GetSidSubAuthorityCount(psid)
            count = ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_ubyte))[0]
            rid_ptr = advapi32.GetSidSubAuthority(psid, count - 1)
            rid = ctypes.cast(rid_ptr, ctypes.POINTER(wintypes.DWORD))[0]
        finally:
            kernel32.CloseHandle(token)
        if rid >= 0x4000:
            return "system"
        if rid >= 0x3000:
            return "high"
        if rid >= 0x2000:
            return "medium"
        return "low"
    except Exception:
        return "unknown"


def active_console_session() -> int | None:
    """The session id of the interactive console (the desktop the user sees), or
    None if it can't be read / no one is logged on (0xFFFFFFFF)."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes

        sid = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
        if sid == 0xFFFFFFFF:
            return None
        return int(sid)
    except Exception:
        return None


# -- documented refusals (the wall above SYSTEM) -------------------------------

_TI_REFUSAL = (
    "NT SERVICE\\TrustedInstaller is a Windows servicing identity, not an "
    "application privilege. Token theft / impersonation of TrustedInstaller is "
    "refused. secdogie never steals service tokens, never bypasses PPL, and "
    "never uses this path to read protected processes. For operator-allowlisted "
    "SYSTEM work, start already-elevated and use the documented CreateProcessAsUser "
    "path."
)

_EDR_REFUSAL = (
    "Anti-EDR, unhooking, handle-hiding, and memory-scan evasion are not "
    "implemented and will not be. Atlas uses documented Win32 / UI Automation "
    "APIs only (OpenProcess with query/read, Toolhelp, UIA COM). The way this "
    "stays off EDR radar is by not doing malware-like things."
)


def try_impersonate_trusted_installer() -> ElevateResult:
    """Always refused. Exists so callers (and tests) can assert the wall."""
    return ElevateResult(REFUSED_IDENTITY, detail=_TI_REFUSAL)


def try_edr_evasion() -> ElevateResult:
    """Always refused. secdogie does not unhook, hide handles, or scan foreign VA."""
    return ElevateResult(REFUSED_IDENTITY, detail=_EDR_REFUSAL)


# -- the on-machine launch (token dance) ---------------------------------------


def run_as_system(command: str | None, *, show: bool = True,
                  _launcher: Callable[[str, int], ElevateResult] | None = None) -> ElevateResult:
    """Create `command` as SYSTEM in the active interactive session.

    Refuses (honestly, without side effects) unless we're on Windows, already
    elevated, and there's a session to launch into -- see plan_launch. The
    `_launcher` seam lets a test drive the decision/plumbing without the real
    Win32 token calls; production uses `_win_launch_as_system`.
    """
    # Read the environment once and decide from that snapshot, rather than
    # querying twice (which both wastes calls and risks the session changing
    # between the check and the launch).
    session_id = active_console_session()
    decision = plan_launch(
        command,
        elevated=is_elevated(),
        session_id=session_id,
        on_windows=sys.platform.startswith("win"),
    )
    if not decision.ok:
        return ElevateResult(decision.reason, detail=_refusal_detail(decision.reason))
    # decision.ok guarantees session_id is not None (plan_launch's NO_SESSION check).
    launcher = _launcher or _win_launch_as_system
    try:
        return launcher(command, session_id, show=show)  # type: ignore[call-arg]
    except Exception as e:
        return ElevateResult(FAILED, detail=str(e))


def _refusal_detail(reason: str) -> str:
    return {
        UNSUPPORTED: "running SYSTEM commands is only supported on Windows",
        NOT_ELEVATED: (
            "this needs Administrator: start secdogie-agent elevated (right-click -> Run as "
            "administrator) first. It acquires SYSTEM from an admin token -- it does not bypass UAC."
        ),
        NO_SESSION: "no interactive desktop session to launch into",
        BAD_COMMAND: "no command given to run",
    }.get(reason, reason)


def _win_launch_as_system(command: str, session_id: int, *, show: bool = True) -> ElevateResult:
    """The real thing (on-machine only): duplicate winlogon's SYSTEM token, retarget
    it at `session_id`, and CreateProcessAsUser into winsta0\\default so the process
    lands on the visible desktop. Isolated here so it's the single place to adjust
    for a Win32 detail; the decision to run it already passed plan_launch."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32
    userenv = ctypes.windll.userenv

    # 1. Give ourselves SeDebugPrivilege so we may open winlogon's token.
    _enable_privilege("SeDebugPrivilege")

    # 2. Find a SYSTEM process to borrow a token from -- winlogon.exe always is.
    pid = _find_process_pid("winlogon.exe")
    if pid is None:
        return ElevateResult(FAILED, detail="could not find winlogon.exe to borrow a SYSTEM token")

    PROCESS_QUERY_INFORMATION = 0x0400
    proc = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not proc:
        return ElevateResult(FAILED, detail="OpenProcess(winlogon) failed")

    dup = wintypes.HANDLE()
    try:
        src = wintypes.HANDLE()
        TOKEN_DUPLICATE = 0x0002
        if not advapi32.OpenProcessToken(proc, TOKEN_DUPLICATE, ctypes.byref(src)):
            return ElevateResult(FAILED, detail="OpenProcessToken(winlogon) failed")
        try:
            # 3. Duplicate it as a PRIMARY token we can launch a process with.
            SecurityImpersonation = 2
            TokenPrimary = 1
            TOKEN_ALL_ACCESS = 0xF01FF
            if not advapi32.DuplicateTokenEx(
                src, TOKEN_ALL_ACCESS, None, SecurityImpersonation, TokenPrimary, ctypes.byref(dup)
            ):
                return ElevateResult(FAILED, detail="DuplicateTokenEx failed")
        finally:
            kernel32.CloseHandle(src)

        # 4. Point the token at the interactive session so the window is visible.
        TokenSessionId = 12
        sid = wintypes.DWORD(session_id)
        advapi32.SetTokenInformation(dup, TokenSessionId, ctypes.byref(sid), ctypes.sizeof(sid))

        # 5. Launch on winsta0\default with the user's environment block.
        env = ctypes.c_void_p()
        userenv.CreateEnvironmentBlock(ctypes.byref(env), dup, False)
        try:
            si = _STARTUPINFO()
            si.cb = ctypes.sizeof(si)
            si.lpDesktop = "winsta0\\default"
            si.dwFlags = 0x00000001  # STARTF_USESHOWWINDOW
            si.wShowWindow = 5 if show else 0  # SW_SHOW / SW_HIDE
            pi = _PROCESS_INFORMATION()

            CREATE_UNICODE_ENVIRONMENT = 0x00000400
            CREATE_NEW_CONSOLE = 0x00000010
            ok = advapi32.CreateProcessAsUserW(
                dup, None, ctypes.c_wchar_p(command), None, None, False,
                CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_CONSOLE, env, None,
                ctypes.byref(si), ctypes.byref(pi),
            )
            if not ok:
                err = kernel32.GetLastError()
                return ElevateResult(FAILED, detail=f"CreateProcessAsUser failed (error {err})")
            launched_pid = int(pi.dwProcessId)
            kernel32.CloseHandle(pi.hThread)
            kernel32.CloseHandle(pi.hProcess)
            return ElevateResult(LAUNCHED, pid=launched_pid, detail=f"started as SYSTEM (pid {launched_pid})")
        finally:
            if env:
                userenv.DestroyEnvironmentBlock(env)
    finally:
        if dup:
            kernel32.CloseHandle(dup)
        kernel32.CloseHandle(proc)


# -- small Win32 helpers (on-machine) ------------------------------------------


def _enable_privilege(name: str) -> None:
    """Enable a named privilege on our own token. Best-effort."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    token = wintypes.HANDLE()
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_QUERY = 0x0008
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(token)
    ):
        return
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = 0x00000002  # SE_PRIVILEGE_ENABLED
        advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
    finally:
        kernel32.CloseHandle(token)


def _find_process_pid(image_name: str) -> int | None:
    """First PID whose image matches `image_name` (case-insensitive), via a
    Toolhelp snapshot. None if not found."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return None
        target = image_name.lower()
        while True:
            if entry.szExeFile.lower() == target:
                return int(entry.th32ProcessID)
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                return None
    finally:
        kernel32.CloseHandle(snap)


# ctypes structures used by _win_launch_as_system (module level so they're only
# built once; harmless to define off Windows -- they reference only ctypes).
try:
    import ctypes as _ctypes
    from ctypes import wintypes as _wintypes

    class _STARTUPINFO(_ctypes.Structure):
        _fields_ = [
            ("cb", _wintypes.DWORD),
            ("lpReserved", _wintypes.LPWSTR),
            ("lpDesktop", _wintypes.LPWSTR),
            ("lpTitle", _wintypes.LPWSTR),
            ("dwX", _wintypes.DWORD), ("dwY", _wintypes.DWORD),
            ("dwXSize", _wintypes.DWORD), ("dwYSize", _wintypes.DWORD),
            ("dwXCountChars", _wintypes.DWORD), ("dwYCountChars", _wintypes.DWORD),
            ("dwFillAttribute", _wintypes.DWORD),
            ("dwFlags", _wintypes.DWORD),
            ("wShowWindow", _wintypes.WORD), ("cbReserved2", _wintypes.WORD),
            ("lpReserved2", _ctypes.POINTER(_ctypes.c_byte)),
            ("hStdInput", _wintypes.HANDLE), ("hStdOutput", _wintypes.HANDLE),
            ("hStdError", _wintypes.HANDLE),
        ]

    class _PROCESS_INFORMATION(_ctypes.Structure):
        _fields_ = [
            ("hProcess", _wintypes.HANDLE), ("hThread", _wintypes.HANDLE),
            ("dwProcessId", _wintypes.DWORD), ("dwThreadId", _wintypes.DWORD),
        ]
except Exception:  # pragma: no cover - ctypes.wintypes is unavailable on some non-Windows builds
    _STARTUPINFO = None  # type: ignore[assignment,misc]
    _PROCESS_INFORMATION = None  # type: ignore[assignment,misc]
