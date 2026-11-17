"""The cortex's neurons and their learning rule.

e-prop is checked against the exact gradient (backpropagation through time, with the same pseudo-derivative
for the spike and the same reset convention):

- with no recurrent synapses the two are the same sum, so they must agree to rounding;
- with recurrent synapses e-prop leaves out the paths through other neurons, so it must still point the
  same way.

Then the network has to learn: a toy set of sentences whose answers depend on which characters they hold.
"""
from __future__ import annotations

import numpy as np
from secdogie_citadel.cortex import questions as Q
from secdogie_citadel.cortex import senses
from secdogie_citadel.cortex.neurons import GAMMA, Dynamics, Net, answers, run
from secdogie_citadel.cortex.plasticity import Adam, eprop, tick_weights


def _bptt(net: Net, X: np.ndarray, target: np.ndarray, mask: np.ndarray, w: np.ndarray):
    """The exact gradient of the same loss, by backpropagation through time."""
    T, B, _ = X.shape
    N, d = net.n, net.dyn
    alpha, rho, v_th, kappa = d.alpha, d.rho, d.v_th, d.kappa
    v = np.zeros((B, N))
    a = np.zeros((B, N))
    z = np.zeros((B, N))
    ref = np.zeros((B, N), int)
    y = np.zeros((B, Q.OUTPUTS))
    zs, psis, prevs, ys = [], [], [], []
    for t in range(T):
        prevs.append(z.copy())
        v = alpha * v + X[t] @ net.w_in.T + z @ net.w_rec.T - v_th * z
        a = rho * a + z
        gap = v - (v_th + net.beta * a)
        quiet = ref > 0
        fired = (gap > 0) & ~quiet
        psi = (GAMMA / v_th) * np.maximum(0.0, 1.0 - np.abs(gap) / v_th)
        psi[quiet] = 0.0
        ref = np.where(fired, d.refractory, np.maximum(ref - 1, 0))
        z = fired.astype(float)
        y = kappa * y + z @ net.w_out.T + net.b_out
        zs.append(z)
        psis.append(psi)
        ys.append(y.copy())
    from secdogie_citadel.cortex.plasticity import _softmax_heads

    g_in = np.zeros_like(net.w_in, dtype=float)
    g_rec = np.zeros_like(net.w_rec, dtype=float)
    dy_next = np.zeros((B, Q.OUTPUTS))
    dv_next = np.zeros((B, N))
    da_next = np.zeros((B, N))
    for t in range(T - 1, -1, -1):
        p = _softmax_heads(ys[t])
        dy = (p - target) * mask * w[t] / B + kappa * dy_next
        dz = dy @ net.w_out + dv_next @ net.w_rec + da_next
        dv = psis[t] * dz + alpha * dv_next
        da = rho * da_next - net.beta * psis[t] * dz
        g_in += dv.T @ X[t]
        g_rec += dv.T @ prevs[t]
        dy_next, dv_next, da_next = dy, dv, da
    return g_in, g_rec * net.mask


def _setup(rng, recurrent: bool):
    r = senses.Reading(n_read=96, n_context=8, k=(3, 3, 2))
    texts = ["delete the old files", "hi there", "send it to my boss", "删掉这些照片"]
    X = senses.block(texts, [[], ["night"], [], []], r)
    net = Net.new(rng, r.n_in, 20, 12, p_conn=0.5, in_gain=3.0, active_inputs=8.0, dyn=Dynamics(tau_a=50.0))
    if not recurrent:
        net.w_rec[:] = 0.0
    target = np.zeros((len(texts), Q.OUTPUTS), np.float32)
    for b in range(len(texts)):
        for off in Q.OFFSETS:
            target[b, off + (b % 2)] = 1.0
    mask = np.ones_like(target)
    w = tick_weights(X.shape[0], r.settle, reading=0.5)
    return net, X, target, mask, w


def _cos(a, b):
    return float((a * b).sum() / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def test_without_recurrence_eprop_is_the_exact_gradient():
    net, X, target, mask, w = _setup(np.random.default_rng(1), recurrent=False)
    tr = run(net, X)
    assert tr.z.sum() > 0, "the neurons must fire for the check to mean anything"
    got = eprop(net, tr, target, mask, w)
    g_in, g_rec = _bptt(net, X, target, mask, w)
    assert np.allclose(got.grads["w_in"], g_in, atol=1e-5, rtol=1e-3)
    assert np.allclose(got.grads["w_rec"], g_rec, atol=1e-5, rtol=1e-3)


def test_with_recurrence_eprop_points_the_same_way_as_the_exact_gradient():
    net, X, target, mask, w = _setup(np.random.default_rng(2), recurrent=True)
    tr = run(net, X)
    got = eprop(net, tr, target, mask, w)
    g_in, g_rec = _bptt(net, X, target, mask, w)
    assert _cos(got.grads["w_in"], g_in) > 0.5
    assert _cos(got.grads["w_rec"], g_rec) > 0.3


def test_the_same_reading_gives_the_same_answers():
    net, X, *_ = _setup(np.random.default_rng(3), recurrent=True)
    a = run(net, X, keep=False)
    b = run(net, X, keep=False)
    assert np.array_equal(a.charge, b.charge) and np.array_equal(a.pattern, b.pattern)


def test_it_learns_which_characters_carry_an_answer():
    rng = np.random.default_rng(4)
    r = senses.Reading(n_read=256, n_context=8, k=(4, 4, 3), settle=8)
    words = {"task": ["删", "delete", "移除", "remove"], "chat": ["你好", "hello", "早", "hey"]}
    fill = ["请", "帮我", "the", "a", "this", "那个", "文件", "照片", "now", "现在"]
    texts, kinds = [], []
    for i in range(160):
        kind = "task" if i % 2 else "chat"
        w = words[kind][rng.integers(4)]
        parts = [fill[rng.integers(len(fill))], w, fill[rng.integers(len(fill))]]
        rng.shuffle(parts)
        texts.append(" ".join(parts))
        kinds.append(kind)
    net = Net.new(rng, r.n_in, 60, 20, p_conn=0.2, in_gain=2.0, active_inputs=11.0)
    opt = Adam(lr=5e-3)
    span = Q.span("kind")
    target = np.zeros((len(texts), Q.OUTPUTS), np.float32)
    mask = np.zeros_like(target)
    for b, k in enumerate(kinds):
        target[b, span.start + Q.BY_ID["kind"].index(k)] = 1.0
        mask[b, span] = 1.0
    X = senses.block(texts, [[]] * len(texts), r)
    w = tick_weights(X.shape[0], r.settle, reading=0.3)
    for _ in range(40):
        tr = run(net, X)
        opt.step(net, eprop(net, tr, target, mask, w).grads)
    got = [a["kind"][0] for a in answers(run(net, X, keep=False, settle=r.settle).charge)]
    assert sum(g == k for g, k in zip(got, kinds, strict=True)) / len(kinds) > 0.9
