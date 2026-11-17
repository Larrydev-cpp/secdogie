"""A cortex's life: birth from the first lessons, and growing up generation by generation.

- ``birth``: a new cortex learns the birth curriculum (the lessons set aside for checking excepted), its
  familiarity memory learns their words, and it takes the exam.
- ``grow``: rounds of self-improvement (``rsi.improve``). Every adopted generation is saved under the
  lineage folder with its record, and the growth curve (each generation's exam marks) is kept beside them.
- ``BORN``: the generation shipped with SecDogie, so every installation starts from the same, examined
  generation 0 and grows from there on its own devices.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import curriculum as C
from . import exam as E
from .core import Cortex
from .improver import Improver
from .rsi import examine, improve

HERE = Path(__file__).resolve().parent
BORN = HERE / "born"


def birth(*, seed: int = 0, epochs: int | None = None, genome: dict | None = None, say: Callable[[str], None] = print) -> Cortex:
    lessons, held = C.split(C.load())
    c = Cortex.new(genome, seed=seed)
    c.familiar.learn(x.text for x in lessons)
    rng = np.random.default_rng(seed)
    t = time.monotonic()
    slept = c.sleep(lessons, rng, epochs=epochs or int(c.genome["epochs"]))
    replies = [x for x in lessons if x.reply]  # what was said back is remembered as it was said, to be recalled
    keys = c.patterns([x.text for x in replies], [x.context for x in replies])
    for x, k in zip(replies, keys, strict=True):
        c.memory.keep(x, k)
    say(f"born from {len(lessons)} lessons in {time.monotonic() - t:.0f}s (loss {slept.loss:.3f}); {len(replies)} replies kept")
    m = examine(c, E.load(), held)
    c.scores = m.view()
    c.lineage = [{"op": "birth", "lessons": len(lessons), "at": time.time()}]
    say(f"generation 0: exam {m.exam.right}/{m.exam.n} ({m.exam.accuracy:.1%}), wrong go-aheads {m.exam.wrong_go}, "
        f"held back when fine {m.exam.over_cautious}, set-aside lessons {m.view()['held']:.1%}")
    return c


def grow(c: Cortex, rounds: int, folder: str | Path, *, seed: int = 1, say: Callable[[str], None] = print,
         improver: Improver | None = None) -> tuple[Cortex, Improver, list[dict]]:
    folder = Path(folder)
    lessons, held = C.split(C.load())
    items = E.load()
    rng = np.random.default_rng(seed)
    imp = improver or Improver.new(seed)
    curve_path = folder / "growth.jsonl"
    archive = [c]
    curve = [{"generation": c.number, "digest": c.digest()[:12], **c.scores, "op": "start", "neurons": c.net.n}]
    base = None
    for k in range(rounds):
        t = time.monotonic()
        r = improve(c, lessons, held, items, imp, rng, archive=archive, base=base)
        tried = ", ".join(f"{x.op} {x.gain:+.3f} ({x.outcome})" for x in r.tried)
        if r.child is not None:
            c = r.child
            archive = (archive + [c])[-6:]
            c.save(folder / f"gen-{c.number:04d}")
            curve.append({"generation": c.number, "digest": c.digest()[:12], **c.scores, "op": c.lineage[-1]["op"], "neurons": c.net.n})
            base = None
            say(f"round {k + 1}: generation {c.number} adopted ({c.lineage[-1]['op']}); exam {c.scores['accuracy']:.1%}, "
                f"wrong go-aheads {c.scores['wrong_go']}; tried {tried}; {time.monotonic() - t:.0f}s")
        else:
            say(f"round {k + 1}: no change adopted; tried {tried}; {time.monotonic() - t:.0f}s")
        folder.mkdir(parents=True, exist_ok=True)
        with curve_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"round": k + 1, "generation": c.number, "tried": [vars(x) for x in r.tried]}, ensure_ascii=False) + "\n")
        (folder / "improver.json").write_text(imp.dumps(), encoding="utf-8")
    (folder / "curve.json").write_text(json.dumps(curve, ensure_ascii=False, indent=1), encoding="utf-8")
    return c, imp, curve


def shipped() -> Cortex:
    """The generation that comes with SecDogie."""
    return Cortex.load(BORN)


__all__ = ["BORN", "birth", "grow", "shipped"]
