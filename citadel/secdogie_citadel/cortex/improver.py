"""The improver: a small network that learns which changes make the cortex better.

Every round of self-improvement tries a few changes to the cortex (``rsi.OPERATORS``). Which ones are worth
trying depends on what is wrong with it: a cortex that knows too little benefits from rehearsal, one that
has run out of room from growing, one whose learning has gone stiff from renewal. The improver is told what
the self-examination found and which change was tried, and learns from how much the exam score actually
moved. The longer it runs, the better it picks, so less is spent on changes that do not help: the method of
improving improves too. Its own settings (how many changes per round, how often to try something unlikely,
how far to mutate) are genes that the loop itself can change.

It is a two-layer network of rate neurons, learned by gradient steps on its own record. With no record yet
it predicts the same for every change and the choice is spread evenly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np

OPERATORS = ("rehearse", "grow", "prune", "renew", "learning", "structure", "decide", "crossover", "improver")
N_FEATURES = 22
HIDDEN = 16


@dataclass
class Improver:
    w1: np.ndarray
    b1: np.ndarray
    w2: np.ndarray
    b2: float = 0.0
    record: list = field(default_factory=list)  # [features, operator, gain]

    @classmethod
    def new(cls, seed: int = 0) -> Improver:
        rng = np.random.default_rng(seed)
        n_in = N_FEATURES + len(OPERATORS)
        return cls((rng.standard_normal((HIDDEN, n_in)) * 0.1).astype(np.float64), np.zeros(HIDDEN),
                   (rng.standard_normal(HIDDEN) * 0.01).astype(np.float64))

    @staticmethod
    def _x(features: list[float], op: str) -> np.ndarray:
        f = np.zeros(N_FEATURES)
        f[:min(N_FEATURES, len(features))] = features[:N_FEATURES]
        o = np.zeros(len(OPERATORS))
        o[OPERATORS.index(op)] = 1.0
        return np.concatenate([f, o])

    def predict(self, features: list[float], op: str) -> float:
        h = np.tanh(self.w1 @ self._x(features, op) + self.b1)
        return float(self.w2 @ h + self.b2)

    def observe(self, features: list[float], op: str, gain: float, *, steps: int = 200, lr: float = 0.05) -> None:
        """Remember what this change did, and learn again from the whole record."""
        self.record.append([list(map(float, features)), op, float(gain)])
        X = np.array([self._x(f, o) for f, o, _ in self.record])
        y = np.array([g for _, _, g in self.record]) * 10.0  # gains are a few points of score: scale them up
        for _ in range(steps):
            h = np.tanh(X @ self.w1.T + self.b1)
            err = h @ self.w2 + self.b2 - y
            g2 = h.T @ err / len(y)
            gh = np.outer(err, self.w2) * (1 - h * h)
            self.w2 -= lr * g2
            self.b2 -= lr * float(err.mean())
            self.w1 -= lr * gh.T @ X / len(y)
            self.b1 -= lr * gh.mean(axis=0)

    def choose(self, features: list[float], k: int, explore: float, rng: np.random.Generator, allowed=OPERATORS) -> list[str]:
        """``k`` changes to try: the most promising, and, with chance ``explore`` each, one picked at random."""
        ops = list(allowed)
        scored = sorted(ops, key=lambda o: -self.predict(features, o))
        out: list[str] = []
        for _ in range(min(k, len(ops))):
            pool = [o for o in ops if o not in out]
            if rng.random() < explore or not self.record:
                out.append(pool[int(rng.integers(len(pool)))])
            else:
                out.append(next(o for o in scored if o not in out))
        return out

    def view(self) -> dict:
        return {"tries": len(self.record), "by_change": {o: [round(g, 4) for _, op, g in self.record if op == o] for o in OPERATORS}}

    def dumps(self) -> str:
        return json.dumps({"w1": self.w1.tolist(), "b1": self.b1.tolist(), "w2": self.w2.tolist(), "b2": self.b2,
                           "record": self.record})

    @classmethod
    def loads(cls, s: str) -> Improver:
        d = json.loads(s)
        return cls(np.array(d["w1"]), np.array(d["b1"]), np.array(d["w2"]), float(d["b2"]), list(d["record"]))


__all__ = ["Improver", "N_FEATURES", "OPERATORS"]
