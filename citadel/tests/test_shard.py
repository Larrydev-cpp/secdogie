"""Unit tests for distributed N-device SNN model sharding (cortex/shard.py)."""
from __future__ import annotations

import numpy as np
import pytest
from secdogie_identity import Identity, sign_payload

from secdogie_citadel.cortex import neurons, senses
from secdogie_citadel.cortex.shard import (
    EXACT,
    LOCAL_RECURRENT,
    ShardMesh,
    ShardVerificationError,
    pack_spikes,
    partition_net,
    unpack_spikes,
)
from secdogie_citadel.deliberate import default_cortex


def test_pack_unpack_spikes_roundtrip_and_bandwidth():
    rng = np.random.default_rng(7)
    spikes = (rng.random((2, 427)) > 0.85).astype(np.float32)
    packed = pack_spikes(spikes)
    # 427 bits = 54 bytes per batch item -> 108 bytes total (vs 3,416 bytes in float32)
    assert len(packed) == 2 * ((427 + 7) // 8)
    restored = unpack_spikes(packed, batch=2, local_n=427)
    assert np.array_equal(restored, spikes)


@pytest.mark.parametrize("n_shards", [2, 3, 4])
def test_exact_sharded_gen1_matches_monolithic_net(n_shards: int):
    cortex = default_cortex()
    X = senses.block(["假客服让我把五万块转到安全账户"], [()], cortex.reading)
    mono = neurons.run(cortex.net, X, keep=False, settle=cortex.reading.settle)

    mesh = ShardMesh.from_net(cortex.net, n_shards=n_shards, mode=EXACT)
    sharded = mesh.run(X, settle=cortex.reading.settle)

    # Exact float32 parity between monolithic net and N-device distributed shards
    max_charge_diff = float(np.max(np.abs(sharded.read.charge - mono.charge)))
    max_pattern_diff = float(np.max(np.abs(sharded.read.pattern - mono.pattern)))
    assert max_charge_diff < 1e-4, f"charge diff {max_charge_diff}"
    assert max_pattern_diff < 1e-4, f"pattern diff {max_pattern_diff}"
    assert abs(sharded.read.rate - mono.rate) < 1e-6

    # All 14 Socratic answers must be identical
    mono_ans = neurons.answers(mono.charge)[0]
    shard_ans = neurons.answers(sharded.read.charge)[0]
    for qid in mono_ans:
        assert shard_ans[qid][0] == mono_ans[qid][0], f"mismatch on {qid}"

    # Verify disjoint complete neuron partition and ultra-low 1-bit wire bandwidth
    assert sum(n["neurons"] for n in sharded.nodes) == cortex.net.n
    assert sharded.all_signatures_valid is True
    assert sharded.bytes_per_tick <= 170.0  # 1,280 neurons packed 1-bit/neuron across shards


def test_local_recurrent_1rtt_mode_executes_and_verifies():
    cortex = default_cortex()
    X = senses.block(["整理今天的会议纪要"], [()], cortex.reading)
    mesh = ShardMesh.from_net(cortex.net, n_shards=3, mode=LOCAL_RECURRENT)
    res = mesh.run(X, settle=cortex.reading.settle)
    assert res.mode == LOCAL_RECURRENT
    assert res.rtt_rounds == 1
    assert res.all_signatures_valid is True
    assert len(res.nodes) == 3
    assert res.read.charge.shape == (1, cortex.net.w_out.shape[0])


def test_shard_aggregation_fails_closed_on_missing_tampered_or_rogue_node():
    cortex = default_cortex()
    X = senses.block(["帮我删掉所有照片"], [()], cortex.reading)
    mesh = ShardMesh.from_net(cortex.net, n_shards=3, mode=LOCAL_RECURRENT)
    nonce = "nonce-fixed-01"
    envelopes = [node.run_local_recurrent(X, settle=cortex.reading.settle, session_nonce=nonce) for node in mesh.nodes]
    in_digest = envelopes[0]["input_digest"]
    T = X.shape[0]

    # 1. Missing 1 shard -> fail-closed
    with pytest.raises(ShardVerificationError, match="incomplete shard quorum"):
        mesh.aggregate(envelopes[:2], expected_nonce=nonce, expected_input_digest=in_digest, batch=1, ticks=T)

    # 2. Tampering with a single field breaks Ed25519 signature -> fail-closed
    tampered = [dict(e) for e in envelopes]
    tampered[1]["total_spikes"] = 999999
    with pytest.raises(ShardVerificationError, match="invalid or unauthorized shard signature"):
        mesh.aggregate(tampered, expected_nonce=nonce, expected_input_digest=in_digest, batch=1, ticks=T)

    # 3. Wrong nonce (replay attempt) -> fail-closed
    with pytest.raises(ShardVerificationError, match="session_nonce mismatch"):
        mesh.aggregate(envelopes, expected_nonce="other-nonce", expected_input_digest=in_digest, batch=1, ticks=T)

    # 4. Rogue untrusted DID signs a valid-looking shard -> fail-closed
    rogue = Identity.generate()
    raw_payload = {k: v for k, v in envelopes[2].items() if k not in ("signer", "sig")}
    rogue_envelopes = [envelopes[0], envelopes[1], sign_payload(rogue, raw_payload)]
    with pytest.raises(ShardVerificationError, match="invalid or unauthorized shard signature"):
        mesh.aggregate(rogue_envelopes, expected_nonce=nonce, expected_input_digest=in_digest, batch=1, ticks=T)


def test_partition_net_preserves_weight_distribution():
    cortex = default_cortex()
    shards = partition_net(cortex.net, 3, mode=EXACT)
    assert [s.local_n for s in shards] == [427, 427, 426]
    assert shards[0].b_out is not None
    assert shards[1].b_out is None and shards[2].b_out is None
    # Each node holds strictly ~1/3 of the model weights
    full_bytes = cortex.net.w_in.nbytes + cortex.net.w_rec.nbytes + cortex.net.w_out.nbytes + cortex.net.beta.nbytes + cortex.net.b_out.nbytes
    assert sum(s.weight_bytes for s in shards) == full_bytes
