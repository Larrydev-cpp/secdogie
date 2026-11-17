"""Plasticity: how the synapses change.

**e-prop** (Bellec et al., *Nature Communications* 2020). Every synapse keeps an *eligibility trace*: a
fading record of how much its two neurons have been firing together, including how the neuron's own
adaptive threshold was moved by its spikes. When the answer neurons turn out wrong, a learning signal is
broadcast back to the neurons that drive them, and each synapse changes by its own eligibility times the
learning signal its neuron receives. Nothing travels backwards through time and every synapse only needs
what happens at its own two ends, as in the brain.

The traces are computed here after the reading is over (sleep and a lesson both have the whole experience
at hand), by running the neuron-local factors backwards. That is the same sum as keeping the traces
forward in time, and far cheaper.

**Neuromodulation.** How much each experience moves the synapses is scaled by a factor of its own: surprise
when the cortex disagreed with the external model, reward and punishment from what the person did.

**Homeostasis.** Neurons that fire far more or far less than their usual rate are pulled back towards it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import questions as Q
from .neurons import Net, Trace


def _softmax_heads(y: np.ndarray) -> np.ndarray:
    p = np.empty_like(y)
    for off, size in zip(Q.OFFSETS, Q.SIZES, strict=True):
        s = y[..., off:off + size]
        e = np.exp(s - s.max(axis=-1, keepdims=True))
        p[..., off:off + size] = e / e.sum(axis=-1, keepdims=True)
    return p


def tick_weights(T: int, settle: int, reading: float = 0.0) -> np.ndarray:
    """How much the answer at each tick counts: the settling ticks at the end, and a little while reading."""
    w = np.full(T, reading / max(1, T - settle), np.float32)
    w[T - settle:] = 1.0 / settle
    return w


@dataclass
class Learned:
    loss: float
    grads: dict[str, np.ndarray]
    rate: float
    rates: np.ndarray = field(repr=False)  # per neuron


def eprop(net: Net, tr: Trace, target: np.ndarray, out_mask: np.ndarray, tick_w: np.ndarray, *,
          modulator: np.ndarray | None = None, rate_target: float = 0.02, rate_reg: float = 0.0) -> Learned:
    """The synapse changes (as gradients) after reading a block of sentences.

    - ``target``: (B, answers) the answer each question should have had, one-hot within each question;
    - ``out_mask``: (B, answers) 1 where the question applies to that sentence;
    - ``tick_w``: (T,) how much each tick's answer counts;
    - ``modulator``: (B,) how much each experience counts.
    """
    T, B, N = tr.z.shape
    kappa, rho = net.dyn.kappa, net.dyn.rho
    mod = np.ones(B, np.float32) if modulator is None else modulator.astype(np.float32)
    p = _softmax_heads(tr.y)
    scale = (tick_w[:, None, None] * mod[None, :, None] * out_mask[None, :, :]) / B
    delta = (p - target[None]) * scale
    with np.errstate(divide="ignore"):
        loss = float(-(np.log(np.maximum(p, 1e-9)) * target[None] * scale).sum())
    filt = np.empty_like(delta)  # what each tick's answers owe to later ticks, through the answer neurons' leak
    acc = np.zeros_like(delta[0])
    for t in range(T - 1, -1, -1):
        acc = delta[t] + kappa * acc
        filt[t] = acc
    flat = filt.reshape(T * B, -1)
    g_out = flat.T @ tr.z.reshape(T * B, N)
    g_b = flat.sum(axis=0)
    signal = filt @ net.w_out  # (T, B, N): the learning signal each neuron receives
    rates = tr.z.mean(axis=(0, 1))
    if rate_reg:
        signal += (rate_reg * (rates - rate_target) / B).astype(np.float32)[None, None, :]
    beta = net.beta[None, :]
    adaptive = bool(np.any(net.beta))
    factor = np.empty_like(signal)
    G = np.zeros((B, N), np.float32)
    for t in range(T - 1, -1, -1):
        psi = tr.psi[t]
        factor[t] = psi * (signal[t] - G)
        if adaptive:
            G = signal[t] * psi * beta + (rho - psi * beta) * G  # the adaptation part of the eligibility, run backwards
    F = factor.reshape(T * B, N)
    g_rec = (F.T @ tr.zhat.reshape(T * B, N)) * net.mask
    g_in = F.T @ tr.xhat.reshape(T * B, -1)
    return Learned(loss, {"w_in": g_in, "w_rec": g_rec, "w_out": g_out, "b_out": g_b}, float(rates.mean()), rates)


class Adam:
    """Per-synapse step sizes (Adam): synapses that have been changing steadily change faster."""

    def __init__(self, lr: float = 2e-3, b1: float = 0.9, b2: float = 0.999, eps: float = 1e-8):
        self.lr, self.b1, self.b2, self.eps = lr, b1, b2, eps
        self.m: dict[str, np.ndarray] = {}
        self.v: dict[str, np.ndarray] = {}
        self.t = 0

    def step(self, net: Net, grads: dict[str, np.ndarray], clip: float = 1.0) -> None:
        self.t += 1
        norm = float(np.sqrt(sum(float((g.astype(np.float64) ** 2).sum()) for g in grads.values())))
        k = min(1.0, clip / norm) if norm > 0 else 1.0
        for name, g in grads.items():
            w = getattr(net, name)
            if name not in self.m or self.m[name].shape != w.shape:
                self.m[name] = np.zeros_like(w)
                self.v[name] = np.zeros_like(w)
            g = g * k
            self.m[name] = self.b1 * self.m[name] + (1 - self.b1) * g
            self.v[name] = self.b2 * self.v[name] + (1 - self.b2) * g * g
            mh = self.m[name] / (1 - self.b1 ** self.t)
            vh = self.v[name] / (1 - self.b2 ** self.t)
            w -= (self.lr * mh / (np.sqrt(vh) + self.eps)).astype(w.dtype)
        net.w_rec *= net.mask


__all__ = ["Adam", "Learned", "eprop", "tick_weights"]
