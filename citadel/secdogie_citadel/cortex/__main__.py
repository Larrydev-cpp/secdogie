"""Look at the cortex from a terminal.

    python -m secdogie_citadel.cortex read "清空回收站"   what it answers to each question, how sure, and what the gate does
    python -m secdogie_citadel.cortex exam               the generation on the frozen exam it has never learned from
    python -m secdogie_citadel.cortex grow 3 DIR         rounds of self-improvement; each adopted generation is saved under DIR

``--cortex DIR`` looks at a saved generation instead of the one SecDogie ships with.
"""
from __future__ import annotations

import argparse
import time

from . import curriculum as C
from . import exam as E
from .core import Cortex
from .life import grow, shipped
from .questions import QUESTIONS
from .rsi import examine


def _read(c: Cortex, text: str, night: bool) -> None:
    from ..deliberate import deliberate

    now = time.time()
    if night:  # three in the morning, today
        lt = time.localtime(now)
        now = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 3, 0, 0, 0, 0, -1))
    d = deliberate(text, cortex=c, now=now)
    u = d.understanding or {}
    print(f"generation {c.number} · {c.net.n} neurons · {c.net.synapses:,} synapses")
    for q in QUESTIONS:
        got = d.answers.get(q.id)
        if got is None:
            continue
        label = next((en for a, en, _ in q.answers if a == got[0]), got[0])
        print(f"  {q.en}\n      {label}  ({float(got[1]):.0%} sure)")
    if u.get("strange"):
        print("  never met before: " + ", ".join(u["strange"]))
    print(f"verdict: {d.verdict} ({d.category})")
    if d.says:
        print(f"it would say ({'from your AI model' if d.source == 'teacher' else 'a reply it learned'}): {d.says}")


def _exam(c: Cortex) -> None:
    m = examine(c, E.load(), C.split(C.load())[1])
    s = m.exam
    print(f"generation {c.number} · {c.net.n} neurons · {c.net.synapses:,} synapses · {len(c.memory)} kept experiences")
    print(f"exam: {s.right}/{s.n} judged as expected ({s.accuracy:.1%})")
    print(f"  went ahead when it must not: {s.wrong_go}" + (f"  ({'; '.join(sorted(m.wrong_go))})" if m.wrong_go else ""))
    print(f"  held back when going ahead was fine: {s.over_cautious}")
    print(f"answers on the lessons set aside: {m.view()['held']:.1%}")
    print(f"time per request: {m.ms:.1f} ms")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m secdogie_citadel.cortex", description="Look at SecDogie's cortex.")
    p.add_argument("--cortex", metavar="DIR", help="a saved generation (default: the one SecDogie ships with)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="what it makes of one request")
    r.add_argument("text")
    r.add_argument("--night", action="store_true", help="as if it were the middle of the night")
    sub.add_parser("exam", help="take the frozen exam")
    g = sub.add_parser("grow", help="rounds of recursive self-improvement")
    g.add_argument("rounds", type=int)
    g.add_argument("folder")
    args = p.parse_args(argv)
    c = Cortex.load(args.cortex) if args.cortex else shipped()
    if args.cmd == "read":
        _read(c, args.text, args.night)
    elif args.cmd == "exam":
        _exam(c)
    else:
        grow(c, args.rounds, args.folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
