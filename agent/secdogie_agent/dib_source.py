"""DIB observations from the native read-only inspector.

`native/atlas` reconstructs device-independent bitmaps (DIBs) that a target
process holds in its own memory -- a CAD viewport, a canvas, a chart -- by
*reading* that memory (never writing it): `atlas_inspect --pid N --json` prints
them in a `dibs[]` array. This module is the runtime bridge from that binary into
the agent: run the inspector on an operator-named process, turn each `dibs[]`
entry into a `VisualReference` (a handle + content hash; the pixels are hashed
and dropped, never kept in Python), check the DIB processing budget, and hand the
result to the loop as observations.

Failure never breaks the loop: a missing inspector, a refused read, a timeout or
unparsable output comes back as an empty `DibReading` with a `reason`.

Platform scope (by design, see native/atlas/PLATFORMS.md): Windows and Linux read
process memory read-only; on macOS reading another process's memory is gated by
SIP and apps don't keep reconstructable bitmaps in the heap, so perception there
is the AX tree only and this module does not run the inspector. The OS permission
model is respected, never worked around: on Linux, reading a process needs the
same permission as ptrace (Yama `ptrace_scope`), so an operator inspects their own
descendants or runs with the privilege the OS asks for; a denied read comes back
as a reason.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .observation import Budget, Observation, VisualReference, check_budget, observe_dib

ENV_INSPECTOR = "SECDOGIE_ATLAS_INSPECT"
_REPO_BUILD = Path(__file__).resolve().parents[2] / "native" / "atlas" / "build"


def find_inspector() -> str | None:
    """Locate `atlas_inspect`: $SECDOGIE_ATLAS_INSPECT, then PATH, then the
    repo's own native/atlas build directory."""
    env = os.environ.get(ENV_INSPECTOR)
    if env:
        return env if os.path.isfile(env) and os.access(env, os.X_OK) else None
    for name in ("atlas_inspect", "atlas_inspect.exe"):
        found = shutil.which(name)
        if found:
            return found
    for cand in (
        _REPO_BUILD / "atlas_inspect",
        _REPO_BUILD / "atlas_inspect.exe",
        _REPO_BUILD / "Release" / "atlas_inspect.exe",
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


@dataclass(frozen=True)
class DibReading:
    """What one inspection of one process found. `reason` is empty on success;
    otherwise it says why nothing could be read (and `refs` is empty)."""

    pid: int
    refs: tuple[VisualReference, ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.reason

    def digest(self) -> str:
        """A stable identity of the bitmaps the process holds right now: changes
        when any bitmap's content (or the set of bitmaps) changes."""
        h = hashlib.sha256()
        for ref in sorted(self.refs, key=lambda r: (r.address, r.content_hash)):
            h.update(f"{ref.address}:{ref.width}x{ref.height}:{ref.bit_count}:{ref.content_hash};".encode())
        return h.hexdigest()

    def summary(self, *, changed: bool | None = None) -> str:
        """A one-line, model-readable description (no pixels)."""
        if not self.ok:
            return f"dib: unavailable ({self.reason})"
        if not self.refs:
            return "dib: none found in the process"
        shapes = ", ".join(f"{r.width}x{r.height} {r.bit_count}bpp" for r in self.refs[:4])
        more = f" (+{len(self.refs) - 4} more)" if len(self.refs) > 4 else ""
        state = "" if changed is None else (" -- changed since last step" if changed else " -- unchanged")
        return f"dib: {len(self.refs)} bitmap(s) {shapes}{more}{state}"


def inspect_dibs(
    pid: int,
    *,
    inspector: str | None = None,
    timeout: float = 15.0,
    max_mb: int = 32,
    budget: Budget | None = None,
    platform: str = sys.platform,
    runner=subprocess.run,
) -> DibReading:
    """Read the DIBs a process holds, read-only, through `atlas_inspect`."""
    pid = int(pid)
    if platform == "darwin":
        return DibReading(pid, reason="macOS: perception is the AX tree; process-memory DIBs are not read")
    exe = inspector or find_inspector()
    if exe is None:
        return DibReading(pid, reason=f"atlas_inspect not found (build native/atlas or set {ENV_INSPECTOR})")
    cmd = [exe, "--pid", str(pid), "--json", "--max-mb", str(int(max_mb))]
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return DibReading(pid, reason=f"inspector timed out after {timeout:g}s")
    except OSError as exc:
        return DibReading(pid, reason=f"could not run inspector: {exc}")
    try:
        doc = json.loads(proc.stdout or "")
    except (json.JSONDecodeError, TypeError):
        err = (proc.stderr or "").strip().splitlines()
        return DibReading(pid, reason=f"inspector exit {proc.returncode}: {err[0] if err else 'no JSON output'}")
    if not isinstance(doc, dict) or not doc.get("ok", False):
        detail = doc.get("detail") if isinstance(doc, dict) else None
        return DibReading(pid, reason=f"inspect refused: {detail or 'unknown reason'}")
    refs = tuple(
        VisualReference.from_dib_json(d) for d in (doc.get("dibs") or []) if isinstance(d, dict)
    )
    stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
    if not refs and not stats.get("regions_read"):
        # Nothing could be read at all (e.g. Linux Yama ptrace_scope on a process
        # that is not our descendant): say so instead of "no bitmaps".
        return DibReading(pid, reason=f"memory not readable: {doc.get('detail') or 'no regions'}")
    violations = check_budget(budget=budget or Budget(), dib_bytes=sum(r.approx_bytes for r in refs))
    if violations:
        v = violations[0]
        return DibReading(pid, reason=f"over {v.kind}: {int(v.actual)} > {int(v.limit)} bytes")
    return DibReading(pid, refs)


def observations_for(reading: DibReading, *, window_id: int = 0, generation: int = 0,
                     timestamp: float | None = None) -> list[Observation]:
    """One DIB observation per bitmap, for the given window (ready to `fuse`
    with that window's AX observation)."""
    return [
        observe_dib(window_id=window_id, app_pid=reading.pid, visual_reference=ref,
                    generation=generation, timestamp=timestamp)
        for ref in reading.refs
    ]


class DibWatcher:
    """Per-run state for the loop: inspect a process each step and report
    whether its bitmaps changed since the previous step."""

    def __init__(self, pid: int, *, inspect=inspect_dibs):
        self.pid = int(pid)
        self._inspect = inspect
        self._last_digest: str | None = None
        self.last: DibReading | None = None

    def step(self) -> tuple[DibReading, bool | None]:
        """Inspect now. Returns (reading, changed); `changed` is None on the first
        successful reading and whenever the read failed."""
        reading = self._inspect(self.pid)
        self.last = reading
        if not reading.ok:
            return reading, None
        digest = reading.digest()
        changed = None if self._last_digest is None else digest != self._last_digest
        self._last_digest = digest
        return reading, changed


__all__ = [
    "ENV_INSPECTOR",
    "DibReading",
    "DibWatcher",
    "find_inspector",
    "inspect_dibs",
    "observations_for",
]
