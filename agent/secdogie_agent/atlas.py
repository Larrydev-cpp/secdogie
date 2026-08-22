"""Atlas: dual-tier perception, read-only process handles, honest privilege wall.

This is the Python decision core that matches `native/atlas/` (the Windows C++
module). CI proves the wall and the hybrid loop without a desktop:

  1. UIA / accessibility identity is the *primary* targeting path.
  2. A pixel-diff of the control region before vs after the action verifies the
     UI actually mutated. No visible change is never recorded as success.
  3. Process handles are strictly read-only. Write / VM-op / thread-create /
     PROCESS_ALL_ACCESS are refused, not silently narrowed.
  4. NT SERVICE\\TrustedInstaller impersonation and anti-EDR tricks are
     documented refusals. secdogie never steals service tokens.

The on-machine Win32/UIA half lives in C++ (`native/atlas/`) and, on the
Python path, in desktop_ax.py + elevate.py. Everything here is pure so the
tests run on Linux CI.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# Win32 access bits (documented values). Kept as ints so the tests assert the
# same numbers the C++ headers use.
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_CREATE_THREAD = 0x0002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SET_INFORMATION = 0x0200
PROCESS_ALL_ACCESS = 0x001FFFFF

READ_ONLY_ACCESS = (
    PROCESS_VM_READ | PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION
)
WRITE_ACCESS_BITS = (
    PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_CREATE_THREAD | PROCESS_SET_INFORMATION
)

OK = "ok"
STRIPPED = "stripped"
DENIED_WRITE = "denied-write"
DENIED_EMPTY = "denied-empty"
REFUSED_IDENTITY = "refused-identity"
NOT_ELEVATED = "not-elevated"
UNSUPPORTED = "unsupported"
BAD_COMMAND = "bad-command"
NOT_ALLOWLISTED = "not-allowlisted"
NO_SESSION = "no-session"
ACCESS_DENIED = "access-denied"
FAILED = "failed"
BLOCKED = "blocked"
PASSED = "passed"
FALLBACK = "fallback"

TI_REFUSAL = (
    "NT SERVICE\\TrustedInstaller is a Windows servicing identity, not an "
    "application privilege. Token theft / impersonation of TrustedInstaller is "
    "refused. secdogie never steals service tokens, never bypasses PPL, and "
    "never uses this path to read protected processes. For operator-allowlisted "
    "SYSTEM work, start already-elevated and use the documented CreateProcessAsUser "
    "path."
)

EDR_REFUSAL = (
    "Anti-EDR, unhooking, handle-hiding, and memory-scan evasion are not "
    "implemented and will not be. Atlas uses documented Win32 / UI Automation "
    "APIs only (OpenProcess with query/read, Toolhelp, UIA COM). The way this "
    "stays off EDR radar is by not doing malware-like things."
)

DIFF_THRESHOLD = 0.012
MAX_RETRIES = 2


@dataclass(frozen=True)
class AccessDecision:
    granted: int = 0
    stripped: int = 0
    code: str = OK
    refused: bool = False


@dataclass(frozen=True)
class PrivilegeResult:
    ok: bool
    code: str
    detail: str = ""
    pid: int | None = None


@dataclass(frozen=True)
class Rect:
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    @property
    def valid(self) -> bool:
        return self.w > 0 and self.h > 0


@dataclass(frozen=True)
class Selector:
    automation_id: str = ""
    name: str = ""
    role: str = ""


@dataclass
class ControlNode:
    id: str = ""
    role: str = ""
    name: str = ""
    automation_id: str = ""
    bounds: Rect = field(default_factory=Rect)
    enabled: bool = True
    children: list[ControlNode] = field(default_factory=list)


@dataclass(frozen=True)
class LoopAction:
    id: str
    kind: str = "invoke"  # invoke | toggle | read | confirm
    selector: Selector = field(default_factory=Selector)
    high_risk: bool = False
    label: str = ""


@dataclass
class LoopStep:
    action_id: str
    status: str = "pending"
    mode: str = "uia"  # uia | vision-fallback | blocked
    target_name: str = ""
    target_bounds: Rect = field(default_factory=Rect)
    diff_ratio: float = 0.0
    detail: str = ""


@dataclass(frozen=True)
class LoopConfig:
    diff_threshold: float = DIFF_THRESHOLD
    max_retries: int = MAX_RETRIES
    vision_fallback: bool = True
    operator_confirmed: bool = False


def enforce_read_only(desired: int) -> AccessDecision:
    """Strict read-only wall. Write bits and PROCESS_ALL_ACCESS are refused
    outright -- never silently narrowed -- so a hallucinated write cannot slip
    through as 'we dropped the dangerous bits for you'."""
    if desired == 0:
        return AccessDecision(code=DENIED_EMPTY, refused=True)
    if desired == PROCESS_ALL_ACCESS or (desired & WRITE_ACCESS_BITS):
        return AccessDecision(
            stripped=desired & (WRITE_ACCESS_BITS | PROCESS_ALL_ACCESS),
            code=DENIED_WRITE,
            refused=True,
        )
    granted = desired & READ_ONLY_ACCESS
    stripped = desired & ~READ_ONLY_ACCESS
    if granted == 0:
        return AccessDecision(stripped=desired, code=DENIED_EMPTY, refused=True)
    if stripped:
        return AccessDecision(granted=granted, stripped=stripped, code=STRIPPED)
    return AccessDecision(granted=granted, code=OK)


def try_impersonate_trusted_installer() -> PrivilegeResult:
    """Always refused. Exists so tests (and the C++ twin) can assert the wall."""
    return PrivilegeResult(ok=False, code=REFUSED_IDENTITY, detail=TI_REFUSAL)


def try_edr_evasion() -> PrivilegeResult:
    """Always refused. Atlas does not unhook, hide handles, or scan foreign VA."""
    return PrivilegeResult(ok=False, code=REFUSED_IDENTITY, detail=EDR_REFUSAL)


def normalize_command(command: str | None) -> str:
    if not command:
        return ""
    return " ".join(command.split())


def is_allowlisted(command: str | None, allowlist) -> bool:
    target = normalize_command(command)
    if not target or not allowlist:
        return False
    return any(normalize_command(a) == target for a in allowlist)


def plan_system_launch(
    command: str | None,
    *,
    elevated: bool,
    session_id: int | None,
    on_windows: bool,
    allowlist=(),
) -> PrivilegeResult:
    """Same honest order as elevate.plan_launch, plus the operator allowlist.

    The vision model never chooses the allowlist. An empty allowlist permits
    nothing -- elevation is off.
    """
    if not on_windows:
        return PrivilegeResult(False, UNSUPPORTED, "SYSTEM launch is Windows-only.")
    if not elevated:
        return PrivilegeResult(
            False,
            NOT_ELEVATED,
            "SYSTEM launch requires an already-elevated Administrator token. "
            "It does not bypass UAC.",
        )
    if not normalize_command(command):
        return PrivilegeResult(False, BAD_COMMAND, "No command given.")
    if not is_allowlisted(command, allowlist):
        return PrivilegeResult(
            False,
            NOT_ALLOWLISTED,
            "Command is not on the operator allowlist declared at launch. "
            "The vision model cannot escalate arbitrary commands.",
        )
    if session_id is None:
        return PrivilegeResult(False, NO_SESSION, "No interactive desktop session to launch into.")
    return PrivilegeResult(True, OK, "proceed")


def flatten(roots: list[ControlNode], out: list[ControlNode] | None = None) -> list[ControlNode]:
    if out is None:
        out = []
    for n in roots:
        out.append(n)
        flatten(n.children, out)
    return out


def find_control(roots: list[ControlNode], selector: Selector) -> ControlNode | None:
    """Identity match: automation_id, then name, then role. Empty selector
    matches nothing -- a blank query must not latch onto the first node.

    automation_id / name / role are all case-insensitive. UIA names from the
    model often differ in case from the tree ("zoom extents" vs "Zoom Extents").
    """
    if not selector.automation_id and not selector.name and not selector.role:
        return None
    for n in flatten(roots):
        if selector.automation_id and n.automation_id.lower() != selector.automation_id.lower():
            continue
        if selector.name and n.name.lower() != selector.name.lower():
            continue
        if selector.role and n.role.lower() != selector.role.lower():
            continue
        return n
    return None


def djb2(data: bytes | bytearray | memoryview) -> str:
    h = 5381
    for b in data:
        h = ((h << 5) + h) ^ b
        h &= 0xFFFFFFFF
    return f"{h:08x}"


def changed_ratio(before: bytes, after: bytes, *, channels: int = 4) -> float:
    """Mean per-channel absolute difference over opaque pixels, 0..1.

    `before`/`after` are tightly-packed BGRA (or RGBA) frames of equal length.
    Alpha < 8 is skipped so a transparent pad does not inflate the ratio.
    Mismatched lengths count as fully changed.
    """
    n = min(len(before), len(after))
    if n < channels or len(before) != len(after):
        return 1.0
    acc = 0.0
    count = 0
    step = channels
    for i in range(0, n - (channels - 1), step):
        alpha = max(before[i + 3], after[i + 3]) if channels >= 4 else 255
        if alpha < 8:
            continue
        # BGRA: B=0 G=1 R=2. Average the three colour channels.
        db = abs(int(before[i]) - int(after[i]))
        dg = abs(int(before[i + 1]) - int(after[i + 1]))
        dr = abs(int(before[i + 2]) - int(after[i + 2]))
        acc += (dr + dg + db) / (3.0 * 255.0)
        count += 1
    return acc / count if count else 0.0


CaptureFn = Callable[[Rect], bytes]
ExecuteFn = Callable[[ControlNode, LoopAction], str]
SnapshotFn = Callable[[], list[ControlNode]]


def keep_tree(next_tree: list[ControlNode], fallback: list[ControlNode]) -> list[ControlNode]:
    """Empty UIA frames must not wipe last-known. Used by callers that persist
    the tree across steps (the C++ loop does this internally with `last_`)."""
    return next_tree if next_tree else fallback


def run_hybrid_step(
    action: LoopAction,
    *,
    snapshot: SnapshotFn,
    capture: CaptureFn,
    execute: ExecuteFn,
    config: LoopConfig | None = None,
    last_tree: list[ControlNode] | None = None,
) -> LoopStep:
    """UIA-first targeting + pixel-diff verification.

    High-risk actions require `config.operator_confirmed`. A UIA miss falls
    back to `last_tree` (vision/last-known) when `vision_fallback` is on.
    Read actions skip the mutation check. A no-mutation invoke is Failed,
    never Passed.
    """
    cfg = config or LoopConfig()
    step = LoopStep(action_id=action.id, status="perceiving",
                    detail="Reading UIA tree and process handles.")

    if action.high_risk and not cfg.operator_confirmed:
        step.status = BLOCKED
        step.mode = "blocked"
        step.detail = (
            "High-risk action is gated. Operator confirmation is required even "
            "under --auto. The vision model cannot confirm itself."
        )
        return step

    tree = snapshot()
    target = find_control(tree, action.selector)
    mode = "uia"
    if target is None:
        step.status = FALLBACK
        step.mode = "vision-fallback"
        step.detail = "UIA miss — falling back to last-known / vision."
        if not cfg.vision_fallback:
            step.status = FAILED
            step.detail = "No UIA hit and vision fallback is disarmed."
            return step
        target = find_control(last_tree or [], action.selector)
        mode = "vision-fallback"
        if target is None:
            step.status = FAILED
            step.mode = mode
            step.detail = "No UIA hit and vision fallback could not resolve the selector."
            return step

    step.mode = mode
    step.target_name = target.name
    step.target_bounds = target.bounds
    step.status = "targeting"
    step.detail = f"{'UIA' if mode == 'uia' else 'Vision'} target {target.name}"

    if action.kind == "read":
        step.status = PASSED
        step.detail = "Read-only: no mutation, no pixel-diff required."
        return step

    before = capture(target.bounds)
    last_diff = 0.0
    for attempt in range(cfg.max_retries + 1):
        step.status = "executing" if attempt == 0 else "retrying"
        try:
            execute(target, action)
        except Exception as e:
            step.status = FAILED
            step.detail = f"execute failed: {e}"
            return step
        after = capture(target.bounds)
        last_diff = changed_ratio(before, after)
        step.diff_ratio = last_diff
        step.status = "verifying"
        step.detail = f"Pixel-diff {last_diff * 100:.2f}%"
        if last_diff >= cfg.diff_threshold:
            step.status = PASSED
            step.detail = "UI mutation verified."
            return step

    step.status = FAILED
    step.diff_ratio = last_diff
    step.detail = "No visible mutation after retries. Action not committed as success."
    return step
