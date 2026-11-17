"""The neurons: a recurrent network of spiking neurons and the answer neurons they drive.

**Spiking neurons** (leaky integrate-and-fire). Each has a membrane potential that leaks back to rest, adds
up the input it receives through its synapses, and fires a spike when it crosses its threshold. Firing
lowers the potential again (reset) and the neuron stays silent for a refractory moment. Some neurons are
*adaptive*: every spike raises their threshold a little, and the raise fades slowly, so they carry what they
have just seen for seconds (a working memory). Synapses are sparse and either excite or inhibit.

**Answer neurons.** Every answer to every Socratic question (``questions.py``) is one neuron that adds up
the spikes it receives and slowly leaks. While a sentence is read, evidence accumulates on them; when the
reading is over, the answer neuron charged highest in each question is the answer.

Time runs in ticks of one millisecond. Everything is plain arrays, so the same network runs anywhere numpy
does.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import questions as Q

GAMMA = 0.3  # how strongly a neuron near its threshold counts as "could have fired" when learning


@dataclass
class Dynamics:
    tau_m: float = 20.0  # membrane time constant, ticks
    tau_a: float = 600.0  # threshold adaptation time constant, ticks
    v_th: float = 1.0
    refractory: int = 2
    kappa: float = 0.97  # how slowly the answer neurons leak

    @property
    def alpha(self) -> float:
        return float(np.exp(-1.0 / self.tau_m))

    @property
    def rho(self) -> float:
        return float(np.exp(-1.0 / self.tau_a))


@dataclass
class Net:
    """The synapses and neurons of one cortex."""

    w_in: np.ndarray  # (neurons, input neurons)
    w_rec: np.ndarray  # (neurons, neurons); zero where there is no synapse
    mask: np.ndarray  # (neurons, neurons) bool: where a synapse exists
    w_out: np.ndarray  # (answer neurons, neurons)
    b_out: np.ndarray  # (answer neurons,)
    beta: np.ndarray  # (neurons,) how much each spike raises the threshold; 0 for neurons that do not adapt
    dyn: Dynamics

    @property
    def n(self) -> int:
        return self.w_rec.shape[0]

    @property
    def n_in(self) -> int:
        return self.w_in.shape[1]

    @property
    def synapses(self) -> int:
        return int(self.mask.sum()) + self.w_in.size + self.w_out.size

    def copy(self) -> Net:
        return Net(self.w_in.copy(), self.w_rec.copy(), self.mask.copy(), self.w_out.copy(), self.b_out.copy(), self.beta.copy(),
                   Dynamics(**vars(self.dyn)))

    @classmethod
    def new(cls, rng: np.random.Generator, n_in: int, n_plain: int, n_adaptive: int, *, p_conn: float = 0.2,
            beta: float = 1.7, in_gain: float = 1.0, rec_gain: float = 1.0, active_inputs: float = 16.0,
            dyn: Dynamics | None = None) -> Net:
        dyn = dyn or Dynamics()
        n = n_plain + n_adaptive
        w_in = (rng.standard_normal((n, n_in)) * in_gain * 0.4 * dyn.v_th / np.sqrt(active_inputs)).astype(np.float32)
        mask = rng.random((n, n)) < p_conn
        np.fill_diagonal(mask, False)
        w_rec = (rng.standard_normal((n, n)) * rec_gain * 0.6 * dyn.v_th / np.sqrt(max(1.0, p_conn * n))).astype(np.float32)
        w_rec *= mask
        w_out = (rng.standard_normal((Q.OUTPUTS, n)) * 0.1 / np.sqrt(n)).astype(np.float32)
        b_out = np.zeros(Q.OUTPUTS, dtype=np.float32)
        b = np.zeros(n, dtype=np.float32)
        b[n_plain:] = beta
        return cls(w_in, w_rec.astype(np.float32), mask, w_out, b_out, b, dyn)


@dataclass
class Trace:
    """What happened while a block of sentences was read: kept for learning."""

    z: np.ndarray  # (T, B, N) spikes
    psi: np.ndarray  # (T, B, N) how close each neuron was to firing (pseudo-derivative)
    zhat: np.ndarray  # (T, B, N) recent spikes of each neuron, leaking with the membrane (for recurrent synapses)
    xhat: np.ndarray  # (T, B, N_in) the same for the input neurons
    y: np.ndarray  # (T, B, answers) the answer neurons' charge


@dataclass
class Read:
    """The result of reading, for deciding (nothing kept for learning)."""

    charge: np.ndarray  # (B, answers): answer neurons, averaged over the settling ticks
    pattern: np.ndarray  # (B, 2N): how the neurons fired over the whole reading, and just at the end
    rate: float  # mean spikes per neuron per tick


def run(net: Net, X: np.ndarray, *, keep: bool = True, settle: int = 12) -> Trace | Read:
    """Read the input spikes ``X`` (ticks, sentences, inputs)."""
    T, B, _ = X.shape
    N, d = net.n, net.dyn
    alpha, rho, v_th, kappa = d.alpha, d.rho, d.v_th, d.kappa
    current = (X.reshape(T * B, -1) @ net.w_in.T).reshape(T, B, N)
    w_rec_t = np.ascontiguousarray(net.w_rec.T)
    w_out_t = np.ascontiguousarray(net.w_out.T)
    v = np.zeros((B, N), np.float32)
    a = np.zeros((B, N), np.float32)
    z = np.zeros((B, N), np.float32)
    ref = np.zeros((B, N), np.int16)
    y = np.zeros((B, net.w_out.shape[0]), np.float32)
    zhat = np.zeros((B, N), np.float32)
    if keep:
        Z = np.empty((T, B, N), np.float32)
        PSI = np.empty((T, B, N), np.float32)
        ZH = np.empty((T, B, N), np.float32)
        XH = np.empty((T, B, X.shape[2]), np.float32)
        Y = np.empty((T, B, y.shape[1]), np.float32)
        xhat = np.zeros((B, X.shape[2]), np.float32)
    else:
        total = np.zeros((B, N), np.float32)
        charge = np.zeros_like(y)
    for t in range(T):
        zhat = alpha * zhat + z  # recent spikes of each neuron, as its synapses onto others feel them
        v = alpha * v + current[t] + z @ w_rec_t - v_th * z
        a = rho * a + z
        gap = v - (v_th + net.beta * a)
        quiet = ref > 0
        fired = (gap > 0) & ~quiet
        psi = (GAMMA / v_th) * np.maximum(0.0, 1.0 - np.abs(gap) / v_th)
        psi[quiet] = 0.0
        ref = np.where(fired, np.int16(d.refractory), np.maximum(ref - 1, 0)).astype(np.int16)
        z = fired.astype(np.float32)
        y = kappa * y + z @ w_out_t + net.b_out
        if keep:
            xhat = alpha * xhat + X[t]
            Z[t], PSI[t], ZH[t], XH[t], Y[t] = z, psi, zhat, xhat, y
        else:
            total += z
            if t >= T - settle:
                charge += y
    if keep:
        return Trace(Z, PSI, ZH, XH, Y)
    pattern = np.concatenate([total / T, zhat * (1.0 - alpha)], axis=1)
    norm = np.linalg.norm(pattern, axis=1, keepdims=True)
    return Read(charge / settle, pattern / np.maximum(norm, 1e-6), float(total.sum() / (T * B * N)))


def shares(charge: np.ndarray) -> np.ndarray:
    """Each question's answer neurons turned into shares that add up to one (softmax within each question)."""
    p = np.empty(charge.shape, np.float64)
    for off, size in zip(Q.OFFSETS, Q.SIZES, strict=True):
        s = charge[..., off:off + size].astype(np.float64)
        e = np.exp(s - s.max(axis=-1, keepdims=True))
        p[..., off:off + size] = e / e.sum(axis=-1, keepdims=True)
    return p


def answers(charge: np.ndarray | None = None, *, p: np.ndarray | None = None) -> list[dict[str, tuple[str, float, tuple[float, ...]]]]:
    """For each sentence, each question's answer: (answer, how sure, the share of every answer)."""
    p = shares(charge) if p is None else p
    out = []
    for row in p:
        got = {}
        for q, off, size in zip(Q.QUESTIONS, Q.OFFSETS, Q.SIZES, strict=True):
            d = row[off:off + size]
            i = int(d.argmax())
            got[q.id] = (q.choices[i], float(d[i]), tuple(round(float(x), 4) for x in d))
        out.append(got)
    return out


__all__ = ["Dynamics", "GAMMA", "Net", "Read", "Trace", "answers", "run", "shares"]
