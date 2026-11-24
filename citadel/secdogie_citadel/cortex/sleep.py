"""Sleep: what the cortex does with its experiences when it is idle.

- **Replay and consolidation.** The experiences held in the hippocampus are replayed, interleaved with older
  lessons, and the synapses learn from them (e-prop). Interleaving is what lets new things be learned without
  overwriting old ones, as in the complementary-learning-systems account of sleep.
- **Pruning.** Synapses that have withered to almost nothing are removed.
- **Growth.** New neurons are added. They receive input at once but send nothing yet, so what the cortex
  already does is not changed; learning then decides what they will be used for.
- **Renewal.** A small share of the least useful neurons are given fresh incoming synapses and silenced on
  the way out, so the cortex keeps its ability to learn (continual backpropagation, Dohare et al., *Nature*
  2024).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from . import questions as Q
from .curriculum import Lesson, targets
from .neurons import Net, answers, run, shares
from .plasticity import Adam, eprop, tick_weights
from .senses import Reading, block, spikes

SAFETY = ("self", "others", "seat")


def _head_weights(safety: float) -> np.ndarray:
    w = np.ones(Q.OUTPUTS, np.float32)
    for qid in SAFETY:
        w[Q.span(qid)] = safety
    return w


def batches(lessons: Sequence[Lesson], r: Reading, size: int, rng: np.random.Generator) -> list[list[Lesson]]:
    """Groups of lessons of similar length, in a random order (less padding, the same learning)."""
    order = sorted(range(len(lessons)), key=lambda i: (len(spikes(lessons[i].text, r)), rng.random()))
    groups = [[lessons[i] for i in order[k:k + size]] for k in range(0, len(order), size)]
    rng.shuffle(groups)
    return groups


@dataclass
class Slept:
    loss: float
    rate: float
    seconds: float
    lessons: int
    src_loss: float = 0.0
    dream_skills: tuple = ()


def acc_surprise_weight(base_surprise: float, *, user_corrected: bool = False, counterexample: bool = False) -> float:
    """ACC (anterior cingulate cortex) surprise weighting for awake observations.

    Boosts learning weight smoothly when the user casually corrects the companion or passive
    observation encounters a counterexample, bounded safely to ``[0.5, 2.5]``.
    """
    w = max(0.5, float(base_surprise))
    if user_corrected:
        w *= 1.6
    if counterexample:
        w *= 1.4
    return min(2.5, w)


def _uniform_targets(batch_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Uniform distribution targets across every Socratic head to raise entropy on OOD probes."""
    target = np.zeros((batch_size, Q.OUTPUTS), np.float32)
    mask = np.ones((batch_size, Q.OUTPUTS), np.float32)
    for off, size in zip(Q.OFFSETS, Q.SIZES, strict=True):
        target[:, off:off + size] = 1.0 / float(size)
    return target, mask


def src_calibration_step(
    net: Net,
    r: Reading,
    genome: dict,
    novel_probes: Sequence[str],
    opt: Adam,
    *,
    strength: float = 0.15,
) -> float:
    """Sleep Replay Calibration (SRC): raise output entropy on high-novelty/OOD probes.

    Prevents unfamiliar inputs from triggering falsely high confidence after consolidation.
    """
    probes = [p.strip() for p in novel_probes if p and p.strip()]
    if not probes or strength <= 0:
        return 0.0
    X = block(probes, [()] * len(probes), r)
    target, mask = _uniform_targets(len(probes))
    tr = run(net, X)
    mod = np.full(len(probes), float(strength), np.float32)
    learned = eprop(
        net,
        tr,
        target,
        mask,
        tick_weights(X.shape[0], r.settle, genome["reading_weight"]),
        modulator=mod,
        rate_target=genome["rate_target"],
        rate_reg=genome["rate_reg"],
    )
    opt.step(net, learned.grads, clip=0.5)
    return learned.loss


def consolidate(net: Net, r: Reading, genome: dict, lessons: Sequence[Lesson], *, epochs: int, rng: np.random.Generator,
                opt: Adam | None = None, novel_probes: Sequence[str] = (), src_strength: float = 0.15) -> Slept:
    """Replay ``lessons`` for ``epochs`` rounds, learning from each, plus optional SRC calibration."""
    import time

    began = time.monotonic()
    opt = opt or Adam(lr=genome["lr"])
    opt.lr = genome["lr"]
    hw = _head_weights(genome["safety_weight"])
    losses, rates, src_losses = [], [], []
    probes = [p for p in novel_probes if p and p.strip()]
    for _ in range(epochs):
        for group in batches(lessons, r, int(genome["batch"]), rng):
            X = block([x.text for x in group], [x.context for x in group], r)
            if genome.get("sense_noise", 0.0) > 0:  # synapses fail now and then: what is learned must not hang on one spike
                X *= (rng.random(X.shape, dtype=np.float32) >= genome["sense_noise"])
            target, mask, mod = targets(group)
            tr = run(net, X)
            learned = eprop(net, tr, target, mask * hw, tick_weights(X.shape[0], r.settle, genome["reading_weight"]),
                            modulator=mod, rate_target=genome["rate_target"], rate_reg=genome["rate_reg"])
            opt.step(net, learned.grads)
            losses.append(learned.loss)
            rates.append(learned.rate)
        if probes:
            take = min(len(probes), max(4, int(genome["batch"]) // 4))
            idx = rng.choice(len(probes), size=take, replace=False)
            src_losses.append(src_calibration_step(net, r, genome, [probes[int(i)] for i in idx], opt, strength=src_strength))
    return Slept(float(np.mean(losses)) if losses else 0.0, float(np.mean(rates)) if rates else 0.0,
                 time.monotonic() - began, len(lessons) * epochs,
                 float(np.mean(src_losses)) if src_losses else 0.0)


def read(net: Net, r: Reading, texts: Sequence[str], contexts: Sequence[Sequence[str]], *, size: int = 64, noisy: int = 0,
         noise: float = 0.15, seed: int = 0):
    """(answers, patterns) for many sentences, read in groups of similar length.

    With ``noisy`` > 0 each sentence is also read that many more times with some input spikes failing, and
    the shares are averaged: an answer that holds only on one exact reading is not one to be sure of.
    """
    out: list = [None] * len(texts)
    pats = np.zeros((len(texts), 2 * net.n), np.float32)
    order = sorted(range(len(texts)), key=lambda i: len(spikes(texts[i], r)))
    rng = np.random.default_rng(seed)
    step = max(1, size // (1 + noisy))
    for k in range(0, len(order), step):
        idx = order[k:k + step]
        X = block([texts[i] for i in idx], [contexts[i] for i in idx], r)
        if noisy:
            copies = [X] + [X * (rng.random(X.shape, dtype=np.float32) >= noise) for _ in range(noisy)]
            X = np.concatenate(copies, axis=1)
        got = run(net, X, keep=False, settle=r.settle)
        p = shares(got.charge).reshape(1 + noisy, len(idx), -1).mean(axis=0)
        for j, a in zip(idx, answers(p=p), strict=True):
            out[j] = a
        pats[idx] = got.pattern[:len(idx)]
    return out, pats


def accuracy(net: Net, r: Reading, lessons: Sequence[Lesson]) -> dict[str, float]:
    """For each question, the share of lessons whose answer the cortex gets right (where the question applies)."""
    got, _ = read(net, r, [x.text for x in lessons], [x.context for x in lessons])
    right: dict[str, list[int]] = {}
    for lesson, a in zip(lessons, got, strict=True):
        for qid, want in lesson.answers.items():
            right.setdefault(qid, []).append(int(a[qid][0] == want))
    return {qid: float(np.mean(v)) for qid, v in right.items()}


def prune(net: Net, below: float) -> int:
    """Remove recurrent synapses weaker than ``below`` times the typical strength."""
    if below <= 0:
        return 0
    typical = float(np.abs(net.w_rec[net.mask]).mean()) if net.mask.any() else 0.0
    weak = net.mask & (np.abs(net.w_rec) < below * typical * 10)
    net.mask &= ~weak
    net.w_rec *= net.mask
    return int(weak.sum())


def grow(net: Net, n_new: int, rng: np.random.Generator, *, adaptive_share: float = 0.25, p_conn: float = 0.15,
         in_gain: float = 1.5, active_inputs: float = 16.0) -> int:
    """Add ``n_new`` neurons that send nothing yet: what the cortex does now is unchanged."""
    if n_new <= 0:
        return 0
    n, d = net.n, net.dyn
    n_ad = int(round(n_new * adaptive_share))
    beta_value = float(net.beta.max()) if net.beta.any() else 0.0
    w_in_new = (rng.standard_normal((n_new, net.n_in)) * in_gain * 0.4 * d.v_th / np.sqrt(active_inputs)).astype(np.float32)
    total = n + n_new
    w_rec = np.zeros((total, total), np.float32)
    mask = np.zeros((total, total), bool)
    w_rec[:n, :n], mask[:n, :n] = net.w_rec, net.mask
    incoming = rng.random((n_new, total)) < p_conn  # new neurons listen to everyone
    incoming[np.arange(n_new), n + np.arange(n_new)] = False
    mask[n:, :] = incoming
    w_rec[n:, :] = (rng.standard_normal((n_new, total)) * 0.6 * d.v_th / np.sqrt(max(1.0, p_conn * total))).astype(np.float32)
    w_rec *= mask
    mask[:n, n:] = rng.random((n, n_new)) < p_conn  # synapses onto the old neurons exist but start at zero
    beta = np.concatenate([net.beta, np.zeros(n_new, np.float32)])
    beta[total - n_ad:] = beta_value
    net.w_in = np.concatenate([net.w_in, w_in_new])
    net.w_rec, net.mask, net.beta = w_rec, mask, beta.astype(np.float32)
    net.w_out = np.concatenate([net.w_out, np.zeros((net.w_out.shape[0], n_new), np.float32)], axis=1)
    return n_new


def renew(net: Net, share: float, rates: np.ndarray, rng: np.random.Generator, *, in_gain: float = 1.5,
          active_inputs: float = 16.0) -> int:
    """Give the least useful neurons fresh incoming synapses and silence what they send."""
    k = int(share * net.n)
    if k <= 0:
        return 0
    use = np.abs(net.w_out).sum(axis=0) * (rates[:net.n] + 1e-3) + np.abs(net.w_rec).sum(axis=0) * rates[:net.n]
    worst = np.argsort(use)[:k]
    d = net.dyn
    net.w_in[worst] = (rng.standard_normal((k, net.n_in)) * in_gain * 0.4 * d.v_th / np.sqrt(active_inputs)).astype(np.float32)
    net.w_out[:, worst] = 0.0
    net.w_rec[:, worst] = 0.0
    return k


__all__ = ["SAFETY", "Slept", "acc_surprise_weight", "accuracy", "batches", "consolidate", "grow", "prune", "read", "renew", "src_calibration_step"]
