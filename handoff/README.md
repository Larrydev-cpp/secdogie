# secdogie-handoff

A **cross-process input-ownership baton**. When two separate processes share one
physical mouse/keyboard, exactly one of them may drive it at any instant — this
package is the lock that enforces that, and the safe handoff between them.

## Why it exists

secdogie already serializes input *within* one process (`agent`'s
`_INPUT_LOCK`): several window-actors in one process can't corrupt each other's
cursor. The hybrid game setup is different — it's **two processes**:

- **Node A** — secdogie's 2D UI logistics (open inventory, craft, switch hotbar,
  drink a potion, reconnect): deterministic macros, secdogie's strong suit.
- **Node B** — the real-time combat controller (relative mouse-look + fire).

A `threading.Lock` can't span processes, so ownership lives in an **OS file
lock** here. That buys two things a plain "handoff message" can't:

1. **Mutual exclusion** — the two processes can never inject input into the same
   instant. It's the cross-process version of the in-process input lock.
2. **Death-safety** — the OS drops the lock automatically when the holder's
   process exits, so a crash mid-combat can't wedge the mouse forever; the other
   node reclaims it.

## The baton, not a broadcast

```
node B trigger fires
   └─▶ B.request_yield("B")          # raise a hand, don't rip the baton out
          node A (between atomic steps, never mid-click)
             └─▶ if A.yield_requested(): A.release()
                    B.acquire("B")    # succeeding IS the proof A released
                       ...combat...
                    B.release()
                       node A.acquire("A")  # logistics resumes
```

`acquire()` succeeding is itself the confirmation the previous owner let go —
the same invariant secdogie uses in `actions.execute` (the next owner gaining
the lock is proof the previous one released), lifted across processes.

## Use

```python
from secdogie_handoff import InputArbiter

arb = InputArbiter()                 # every node points at the same lock dir
                                     # (default ~/.secdogie/handoff)

# Node A, around each atomic macro step:
with arb.hold("node-a"):             # blocks until it owns input, always releases
    do_one_ui_step()
    if arb.yield_requested():        # B wants the baton -> stop after this step
        break

# Node B, when combat triggers:
arb.request_yield("node-b")
if arb.acquire("node-b", timeout=2):
    try:
        run_combat()
    finally:
        arb.release()
```

- `acquire(node_id, timeout=None)` → `bool` (None blocks, 0 is a non-blocking try).
- `release()` — safe to call unconditionally.
- `hold(node_id, timeout=None)` — context manager; raises `TimeoutError` if it
  can't acquire, so you never proceed thinking you own input when you don't.
- `request_yield(requester)` / `yield_requested()` / `clear_yield()` — the
  preemption channel.
- `owner()` — advisory, for logs/status only (can be stale after a crash; the OS
  lock is the source of truth).

## Install

```bash
pip install -e .
```

## Test

```bash
pytest tests/ -q
```

The mutual-exclusion and death-safety tests spawn real child processes — a
same-process test would prove nothing, since POSIX record locks are owned per
process, not per file descriptor.

## Scope

This is only the ownership contract. The nodes themselves — secdogie's
logistics macros (Node A) and the combat controller with its relative
mouse-look input path and target detection (Node B) — live elsewhere. This
package makes it *safe* for them to share the one mouse.
