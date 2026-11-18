"""The cortex: one living decision model, and its generations.

A ``Cortex`` is the neurons and synapses (``neurons.py``), how its senses are wired (``senses.py``), its
genome (``genome.py``), its familiarity memory (``familiar.py``) and its hippocampus (``hippocampus.py``).

- ``understand``: read what the person said and answer the Socratic questions, each answer with how sure it
  is, the unfamiliar stretches, and what was recalled.
- ``learn``: a lesson while awake. It is kept in the hippocampus at once (so it is known from the next
  sentence on), its words become familiar, and the synapses move a little, in proportion to how surprising
  the lesson was.
- ``sleep``: replay what was kept, interleaved with older lessons; prune, renew; read the kept experiences
  again so their keys match the new synapses.
- ``save`` / ``load``: a generation on disk. Its digest is a hash of its synapses, wiring and genome, so a
  generation can be named, signed, compared and restored exactly.

Synapses are kept at half precision (float16), as stored, so a generation that is loaded is exactly the one
that was saved.
"""
from __future__ import annotations

import hashlib
import io
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import curriculum as C
from . import genome as G
from . import sleep as S
from .apprentice import Ledger
from .familiar import Familiarity
from .hippocampus import Hippocampus, Recall, blend
from .neurons import Dynamics, Net, answers, run, shares
from .plasticity import Adam, eprop, tick_weights
from .senses import Reading, block

FORMAT = 1
ELDERS = 2  # how many earlier generations vote on the questions of safety
SAFE = (("self", "yes"), ("others", "harm"), ("seat", "yes"))  # the cautious answer of each question of safety


@dataclass(frozen=True)
class Understanding:
    """What the cortex made of one sentence."""

    text: str
    answers: dict  # question id -> (answer, how sure, shares of every answer)
    novelty: float = 0.0  # share of the sentence never met
    strange: tuple[str, ...] = ()  # the unfamiliar stretches
    recalled: float = 0.0  # how much a kept experience counted (0..1)
    recalled_from: tuple[str, ...] = ()  # the kept lessons it rests on
    generation: str = ""
    reply: str = ""  # what was said back, the last time something read like this was learned
    reply_from: str = ""  # where that reply came from (teacher, birth, person)

    def answer(self, qid: str) -> str:
        return self.answers[qid][0]

    def sure(self, qid: str) -> float:
        return float(self.answers[qid][1])

    def journal(self) -> dict:
        return {"answers": {q: [a[0], round(a[1], 3)] for q, a in self.answers.items()}, "novelty": round(self.novelty, 3),
                "strange": list(self.strange), "recalled": round(self.recalled, 3), "generation": self.generation}


def _f16(a: np.ndarray) -> np.ndarray:
    return a.astype(np.float16).astype(np.float32)


@dataclass
class Cortex:
    reading: Reading
    genome: dict
    net: Net
    familiar: Familiarity
    memory: Hippocampus = field(default_factory=Hippocampus)
    number: int = 0  # which generation
    parent: str = ""  # the digest of the generation it came from
    lineage: list = field(default_factory=list)  # what was done to make it, oldest first
    scores: dict = field(default_factory=dict)
    born: float = 0.0
    opt: Adam | None = field(default=None, repr=False)
    ledger: Ledger = field(default_factory=Ledger)
    elders: list = field(default_factory=list, repr=False)  # the generations before it, newest first (at most ELDERS)

    # ---- making one ---------------------------------------------------------------------------------------------

    @classmethod
    def new(cls, genome: dict | None = None, reading: Reading | None = None, seed: int = 0) -> Cortex:
        g = G.clip(genome or G.default())
        r = reading or Reading()
        rng = np.random.default_rng(seed)
        dyn = Dynamics(tau_m=g["tau_m"], tau_a=g["tau_a"], refractory=int(g["refractory"]), kappa=g["kappa"])
        net = Net.new(rng, r.n_in, int(g["n_plain"]), int(g["n_adaptive"]), p_conn=g["p_conn"], beta=g["beta"],
                      in_gain=g["in_gain"], rec_gain=g["rec_gain"], active_inputs=float(sum(r.k)), dyn=dyn)
        c = cls(r, g, net, Familiarity.new(), born=time.time())
        c.settle()
        return c

    def settle(self) -> None:
        """Synapses at the precision they are stored at."""
        for name in ("w_in", "w_rec", "w_out", "b_out"):
            setattr(self.net, name, _f16(getattr(self.net, name)))
        self.net.w_rec *= self.net.mask

    def copy(self) -> Cortex:
        mem = Hippocampus(list(self.memory.lessons), None if self.memory.keys is None else self.memory.keys.copy(),
                          self.memory.capacity)
        return Cortex(self.reading, dict(self.genome), self.net.copy(), Familiarity(self.familiar.cells.copy()), mem, self.number,
                      self.parent, list(self.lineage), dict(self.scores), self.born, ledger=self.ledger, elders=list(self.elders))

    def descend(self) -> Cortex:
        """A child: a copy of this cortex, with this one (and its elders) voting on the questions of safety."""
        child = self.copy()
        child.elders = ([self.frozen()] + self.elders)[:ELDERS]
        return child

    def frozen(self) -> Cortex:
        """This generation as an elder: its synapses and wiring only."""
        return Cortex(self.reading, dict(self.genome), self.net, self.familiar, Hippocampus(), self.number, self.parent)

    def apply_dynamics(self) -> None:
        g = self.genome
        self.net.dyn = Dynamics(tau_m=g["tau_m"], tau_a=g["tau_a"], refractory=int(g["refractory"]), kappa=g["kappa"])

    # ---- reading ------------------------------------------------------------------------------------------------

    def understand(self, texts: Sequence[str], contexts: Sequence[Sequence[str]] | None = None, *, recall: bool = True
                   ) -> list[Understanding]:
        contexts = contexts or [()] * len(texts)
        got, pats = S.read(self.net, self.reading, texts, contexts)
        p = np.array([[a[q.id][2][i] for q in _QS for i in range(len(q.answers))] for a in got])
        for elder in self.elders:  # on the questions of safety, the most cautious generation's reading counts
            eg, _ = S.read(elder.net, elder.reading, texts, contexts)
            ep = np.array([[a[q.id][2][i] for q in _QS for i in range(len(q.answers))] for a in eg])
            for qid, cautious in SAFE:
                sl = _Q.span(qid)
                k = sl.start + _Q.BY_ID[qid].index(cautious)
                more = ep[:, k] > p[:, k]
                p[more, sl] = ep[more, sl]
        recs: list[Recall] = (self.memory.recall(pats, sharpness=self.genome["recall_sharpness"], trust=self.genome["recall_trust"])
                              if recall and len(self.memory) else [])
        if recs:
            read = p.copy()  # what it read before anything was recalled
            p = blend(p, recs)
            for qid, cautious in SAFE:  # a memory may raise an alarm about safety, never quiet one
                sl = _Q.span(qid)
                k = sl.start + _Q.BY_ID[qid].index(cautious)
                quieter = p[:, k] < read[:, k]
                p[quieter, sl] = read[quieter, sl]
        out = []
        digest = self.digest()[:12]
        for b, (text, a) in enumerate(zip(texts, answers(p=p), strict=True)):
            nov, strange = self.familiar.novelty(text)
            r = recs[b] if recs else None
            src = tuple(self.memory.lessons[i].text for i in r.lessons[:3]) if r is not None and r.weight > 0 else ()
            top = self.memory.lessons[r.lessons[0]] if r is not None and r.weight >= 0.5 and r.lessons else None
            reply, origin = (top.reply, top.source) if top is not None and top.reply else ("", "")
            out.append(Understanding(text, a, nov, strange, r.weight if r is not None else 0.0, src, digest, reply, origin))
        return out

    def patterns(self, texts: Sequence[str], contexts: Sequence[Sequence[str]]) -> np.ndarray:
        return S.read(self.net, self.reading, texts, contexts)[1]

    # ---- learning -----------------------------------------------------------------------------------------------

    def learn(self, lesson: C.Lesson, *, surprise: float = 1.0) -> None:
        """A lesson while awake: kept at once, made familiar, and a small change to the synapses."""
        self.familiar.learn([lesson.text])
        X = block([lesson.text], [lesson.context], self.reading)
        target, mask, _ = C.targets([lesson])
        tr = run(self.net, X)
        learned = eprop(self.net, tr, target, mask, tick_weights(X.shape[0], self.reading.settle, self.genome["reading_weight"]),
                        modulator=np.array([lesson.weight * surprise], np.float32))
        self.opt = self.opt or Adam(lr=self.genome["lr"])
        self.opt.lr = self.genome["lr"] * 0.3  # awake, the synapses move only a little; sleep does the rest
        self.opt.step(self.net, learned.grads)
        self.settle()
        self.memory.keep(lesson, self.patterns([lesson.text], [lesson.context])[0])  # as it reads now, after learning

    def sleep(self, old: Sequence[C.Lesson], rng: np.random.Generator, *, epochs: int | None = None) -> S.Slept:
        """Replay the kept experiences with a share of older lessons, then prune, renew and re-key."""
        g = self.genome
        kept = list(self.memory.lessons)
        n_old = int(round(len(kept) * g["replay_old"] / max(1e-6, 1 - g["replay_old"]))) if kept else len(old)
        pick = [old[i] for i in rng.choice(len(old), size=min(len(old), max(n_old, 1)), replace=False)] if old else []
        self.opt = self.opt or Adam(lr=g["lr"])
        slept = S.consolidate(self.net, self.reading, g, kept + pick, epochs=epochs or int(g["epochs"]), rng=rng, opt=self.opt)
        S.prune(self.net, g["prune_below"])
        self.settle()
        if kept:
            self.memory.rekey(self.patterns([x.text for x in kept], [x.context for x in kept]))
        return slept

    # ---- generations on disk ------------------------------------------------------------------------------------

    def _arrays(self) -> dict[str, np.ndarray]:
        n = self.net
        return {"w_in": n.w_in.astype(np.float16), "w_rec": n.w_rec.astype(np.float16), "mask": np.packbits(n.mask),
                "w_out": n.w_out.astype(np.float16), "b_out": n.b_out.astype(np.float16), "beta": n.beta.astype(np.float32),
                "n": np.array([n.n], np.int64)}

    def _identity(self) -> dict:
        return {"format": FORMAT, "reading": self.reading.view(), "genome": self.genome}

    def digest(self) -> str:
        h = hashlib.sha256()
        for k, v in sorted(self._arrays().items()):
            h.update(k.encode())
            h.update(np.ascontiguousarray(v).tobytes())
        h.update(json.dumps(self._identity(), sort_keys=True).encode())
        return h.hexdigest()

    def record(self) -> dict:
        return {**self._identity(), "digest": self.digest(), "number": self.number, "parent": self.parent,
                "lineage": self.lineage, "scores": self.scores, "born": self.born, "neurons": self.net.n,
                "synapses": self.net.synapses, "kept": len(self.memory)}

    def save(self, folder: str | Path) -> Path:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        np.savez_compressed(buf, **self._arrays())
        (folder / "synapses.npz").write_bytes(buf.getvalue())
        (folder / "cortex.json").write_text(json.dumps(self.record(), ensure_ascii=False, indent=1), encoding="utf-8")
        self.save_memory(folder)
        for i, elder in enumerate(self.elders):
            sub = folder / f"elder-{i}"
            sub.mkdir(exist_ok=True)
            buf = io.BytesIO()
            np.savez_compressed(buf, **elder._arrays())
            (sub / "synapses.npz").write_bytes(buf.getvalue())
            (sub / "cortex.json").write_text(json.dumps(elder.record(), ensure_ascii=False), encoding="utf-8")
        return folder

    def save_memory(self, folder: str | Path) -> None:
        """What changes while awake (kept experiences, familiarity, the ledger), without the synapses."""
        folder = Path(folder)
        with (folder / "memory.jsonl").open("w", encoding="utf-8") as f:
            for x in self.memory.lessons:
                f.write(json.dumps({"text": x.text, "answers": x.answers, "context": list(x.context), "reply": x.reply,
                                    "source": x.source, "weight": x.weight}, ensure_ascii=False) + "\n")
        (folder / "familiar.bin").write_bytes(self.familiar.pack())
        (folder / "ledger.json").write_text(json.dumps(vars(self.ledger), ensure_ascii=False), encoding="utf-8")
        keys = folder / "keys.npy"
        if self.memory.keys is not None and len(self.memory):
            np.save(keys, self.memory.keys.astype(np.float16))
        elif keys.exists():
            keys.unlink()

    @classmethod
    def load(cls, folder: str | Path, *, elders: bool = True) -> Cortex:
        folder = Path(folder)
        rec = json.loads((folder / "cortex.json").read_text(encoding="utf-8"))
        z = np.load(io.BytesIO((folder / "synapses.npz").read_bytes()))
        n = int(z["n"][0])
        mask = np.unpackbits(z["mask"])[:n * n].reshape(n, n).astype(bool)
        g = G.clip(rec["genome"])
        dyn = Dynamics(tau_m=g["tau_m"], tau_a=g["tau_a"], refractory=int(g["refractory"]), kappa=g["kappa"])
        net = Net(z["w_in"].astype(np.float32), z["w_rec"].astype(np.float32), mask, z["w_out"].astype(np.float32),
                  z["b_out"].astype(np.float32), z["beta"].astype(np.float32), dyn)
        fam = folder / "familiar.bin"
        c = cls(Reading.of(rec["reading"]), g, net, Familiarity.unpack(fam.read_bytes()) if fam.exists() else Familiarity.new(), Hippocampus(),
                int(rec.get("number", 0)), rec.get("parent", ""), list(rec.get("lineage", [])), dict(rec.get("scores", {})),
                float(rec.get("born", 0.0)))
        mem = folder / "memory.jsonl"
        lessons = []
        if mem.exists():
            for line in mem.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    lessons.append(C.Lesson(d["text"], d["answers"], tuple(d.get("context", ())), d.get("reply", ""),
                                            d.get("source", ""), float(d.get("weight", 1.0))))
        if lessons:
            c.memory.lessons = lessons
            kf = folder / "keys.npy"
            keys = np.load(kf).astype(np.float32) if kf.exists() else None
            if keys is None or keys.shape != (len(lessons), 2 * c.net.n):
                keys = c.patterns([x.text for x in lessons], [x.context for x in lessons])
            c.memory.rekey(keys)
        if elders:
            c.elders = [cls.load(sub, elders=False) for sub in sorted(folder.glob("elder-*")) if (sub / "cortex.json").exists()]
        led = folder / "ledger.json"
        if led.exists():
            c.ledger = Ledger(**json.loads(led.read_text(encoding="utf-8")))
        if rec.get("digest") and rec["digest"] != c.digest():
            raise ValueError("this generation's synapses do not match its record")
        return c


from . import questions as _Q  # noqa: E402

_QS = _Q.QUESTIONS

__all__ = ["Cortex", "FORMAT", "Understanding", "shares"]
