"""InputArbiter tests. The mutual-exclusion and death-safety guarantees are
cross-PROCESS, so those tests spawn real child processes (a threading.Lock or
two fds in one process wouldn't prove anything -- POSIX record locks are owned
per process). The yield/handoff signaling is just files, so it's tested in
process."""
import multiprocessing as mp
import os

from secdogie_handoff import InputArbiter

# Fork keeps the module-level workers importable in the child without pickling
# closures; it's the Linux/CI default anyway, but pin it so the tests are
# deterministic across environments.
_CTX = mp.get_context("fork")


def _hold_worker(lock_dir, node_id, acquired_evt, release_evt):
    """Child: take the baton, announce it, hold until told to let go."""
    arb = InputArbiter(lock_dir)
    arb.acquire(node_id)
    acquired_evt.set()
    release_evt.wait(5)
    arb.release()


def _acquire_and_die(lock_dir, acquired_evt):
    """Child: take the baton and die WITHOUT releasing, to prove the OS drops it."""
    arb = InputArbiter(lock_dir)
    arb.acquire("dying")
    acquired_evt.set()
    os._exit(0)  # hard exit: no release(), no atexit, nothing runs


def test_acquire_release_roundtrip(tmp_path):
    arb = InputArbiter(str(tmp_path))
    assert arb.acquire("A") is True
    assert arb.held and arb.owner() == "A"
    arb.release()
    assert not arb.held


def test_hold_context_manager_releases_even_on_error(tmp_path):
    arb = InputArbiter(str(tmp_path))
    try:
        with arb.hold("A"):
            assert arb.held
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not arb.held  # released despite the exception


def test_ownership_is_mutually_exclusive_across_processes(tmp_path):
    acquired, release = _CTX.Event(), _CTX.Event()
    p = _CTX.Process(target=_hold_worker, args=(str(tmp_path), "A", acquired, release))
    p.start()
    try:
        assert acquired.wait(5)  # child A holds the baton
        b = InputArbiter(str(tmp_path))
        assert b.acquire("B", timeout=0.3) is False  # cannot take it while A holds
        release.set()
        p.join(5)
        assert b.acquire("B", timeout=2) is True  # now that A let go, B gets it
        b.release()
    finally:
        release.set()
        p.join(5)


def test_lock_frees_when_holder_dies(tmp_path):
    acquired = _CTX.Event()
    p = _CTX.Process(target=_acquire_and_die, args=(str(tmp_path), acquired))
    p.start()
    assert acquired.wait(5)
    p.join(5)
    # The holder died without releasing; the OS must have dropped the lock, or
    # this would block/fail -- that's the anti-wedge guarantee for a combat crash.
    survivor = InputArbiter(str(tmp_path))
    assert survivor.acquire("survivor", timeout=2) is True
    survivor.release()


def test_yield_request_seen_by_owner_and_cleared_on_handoff(tmp_path):
    a = InputArbiter(str(tmp_path))
    b = InputArbiter(str(tmp_path))
    assert a.acquire("A")
    assert a.yield_requested() is None

    b.request_yield("B")               # B raises its hand for the baton
    assert a.yield_requested() == "B"  # owner A sees it and can release at a safe point
    a.release()

    assert b.acquire("B", timeout=1)
    assert b.yield_requested() is None  # the handoff cleared the pending request
    b.release()


def test_owner_is_none_before_any_acquire(tmp_path):
    assert InputArbiter(str(tmp_path)).owner() is None
