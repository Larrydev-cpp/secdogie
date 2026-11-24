"""Distributed SNN model sharding across N paired devices.

Instead of asking a remote device to click "Approve" on every step, SecDogie
partitions the spiking prefrontal cortex (``Net``) across N devices (e.g.
Desktop + Phone + Laptop/NAS). Each device holds ONLY its own disjoint slice of
LIF/ALIF neurons and synapses, exchanges ultra-compact 1-bit packed spikes, and
returns an Ed25519 DID-signed partial readout charge.

Two operating modes:
- ``exact`` (LAN / low-latency P2P): exchanges 1-bit packed binary spikes
  (``np.packbits``: ~160 bytes/tick for 1,280 neurons) at every tick so the
  aggregated readout charge matches monolithic ``neurons.run()`` to float32
  precision (< 1e-5).
- ``local_recurrent`` (WAN / 4G WebRTC): keeps block-diagonal intra-shard
  recurrence so each device runs all T ticks locally and sends a single
  DID-signed partial readout contribution in 1 network round-trip (1-RTT).

Security property (fail-closed):
- Every shard contribution is bound to a fresh ``session_nonce`` and
  ``input_digest`` and signed with the device's Ed25519 DID.
- Missing shards, tampered charges, replayed nonces, or unauthorized DIDs raise
  ``ShardVerificationError``: a compromised single device cannot forge the full
  prefrontal cortex.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from secdogie_identity import Allowlist, Identity, sign_payload, verify_payload

from .neurons import Dynamics, Net, Read

EXACT = "exact"
LOCAL_RECURRENT = "local_recurrent"


class ShardVerificationError(RuntimeError):
    """Raised when a distributed shard response fails cryptographic or structural verification."""


def pack_spikes(spikes: np.ndarray) -> bytes:
    """Pack a binary spike array (B, local_n) into 1-bit-per-neuron bytes."""
    bits = (spikes > 0.5).astype(np.uint8)
    return np.packbits(bits, axis=-1).tobytes()


def unpack_spikes(payload: bytes, *, batch: int, local_n: int) -> np.ndarray:
    """Unpack 1-bit spike bytes back into float32 (batch, local_n)."""
    row_bytes = (local_n + 7) // 8
    expected = batch * row_bytes
    if len(payload) != expected:
        raise ShardVerificationError(f"invalid packed spike length: got {len(payload)}, expected {expected}")
    raw = np.frombuffer(payload, dtype=np.uint8).reshape(batch, row_bytes)
    bits = np.unpackbits(raw, axis=-1)[:, :local_n]
    return bits.astype(np.float32)


def _encode_f32(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype=np.float32).tobytes()).decode("ascii")


def _decode_f32(encoded: str, shape: tuple[int, ...]) -> np.ndarray:
    raw = base64.b64decode(encoded, validate=True)
    arr = np.frombuffer(raw, dtype=np.float32)
    expected = int(np.prod(shape))
    if arr.size != expected:
        raise ShardVerificationError(f"float32 payload size mismatch: got {arr.size}, expected {expected}")
    return arr.reshape(shape).copy()


def _input_digest(X: np.ndarray) -> str:
    arr = np.ascontiguousarray(X, dtype=np.float32)
    h = hashlib.sha256()
    h.update(str(arr.shape).encode("ascii"))
    h.update(arr.tobytes())
    return h.hexdigest()[:32]


@dataclass
class NetShard:
    """One device's disjoint slice [start:end) of a spiking neural network."""

    shard_id: int
    n_shards: int
    start: int
    end: int
    total_neurons: int
    mode: str  # EXACT | LOCAL_RECURRENT
    w_in: np.ndarray  # (local_n, n_in)
    w_rec: np.ndarray  # (local_n, total_neurons) in exact mode, or (local_n, local_n) in local_recurrent
    w_out: np.ndarray  # (outputs, local_n)
    b_out: np.ndarray | None  # (outputs,) only on shard 0 so bias is applied exactly once
    beta: np.ndarray  # (local_n,)
    dyn: Dynamics

    @property
    def local_n(self) -> int:
        return self.end - self.start

    @property
    def n_plain(self) -> int:
        return int(np.sum(self.beta == 0.0))

    @property
    def n_adaptive(self) -> int:
        return int(np.sum(self.beta > 0.0))

    @property
    def weight_bytes(self) -> int:
        total = self.w_in.nbytes + self.w_rec.nbytes + self.w_out.nbytes + self.beta.nbytes
        if self.b_out is not None:
            total += self.b_out.nbytes
        return int(total)

    @property
    def digest(self) -> str:
        h = hashlib.sha256()
        h.update(f"{self.shard_id}/{self.n_shards}:{self.start}-{self.end}:{self.mode}".encode("ascii"))
        for arr in (self.w_in, self.w_rec, self.w_out, self.beta):
            h.update(np.ascontiguousarray(arr, dtype=np.float32).tobytes())
        return h.hexdigest()[:16]


def partition_net(net: Net, n_shards: int, *, mode: str = EXACT) -> list[NetShard]:
    """Partition a monolithic ``Net`` into ``n_shards`` disjoint neuron shards."""
    if n_shards < 1:
        raise ValueError("n_shards must be >= 1")
    if mode not in (EXACT, LOCAL_RECURRENT):
        raise ValueError(f"unsupported shard mode: {mode!r}")
    N = net.n
    if n_shards > N:
        raise ValueError(f"n_shards ({n_shards}) cannot exceed total neurons ({N})")

    base, rem = divmod(N, n_shards)
    shards: list[NetShard] = []
    cursor = 0
    for idx in range(n_shards):
        size = base + (1 if idx < rem else 0)
        start, end = cursor, cursor + size
        cursor = end
        w_rec_slice = (
            net.w_rec[start:end, :].copy()
            if mode == EXACT
            else net.w_rec[start:end, start:end].copy()
        )
        shards.append(
            NetShard(
                shard_id=idx,
                n_shards=n_shards,
                start=start,
                end=end,
                total_neurons=N,
                mode=mode,
                w_in=net.w_in[start:end, :].copy(),
                w_rec=w_rec_slice,
                w_out=net.w_out[:, start:end].copy(),
                b_out=net.b_out.copy() if idx == 0 else None,
                beta=net.beta[start:end].copy(),
                dyn=Dynamics(**vars(net.dyn)),
            )
        )
    return shards


class ShardNode:
    """One device hosting a ``NetShard`` and signing its partial readout with its Ed25519 DID."""

    def __init__(
        self,
        shard: NetShard,
        *,
        identity: Identity | None = None,
        device_name: str = "",
        role: str = "peer",
    ):
        self.shard = shard
        self.identity = identity if identity is not None else Identity.generate()
        self.device_name = device_name or f"node-{shard.shard_id}"
        self.role = role
        self._w_rec_t = np.ascontiguousarray(shard.w_rec.T)
        self._w_out_t = np.ascontiguousarray(shard.w_out.T)
        # Transient tick-sync execution state (for exact mode)
        self._state: dict[str, Any] | None = None

    @property
    def did(self) -> str:
        return self.identity.did

    def begin(self, X: np.ndarray, *, settle: int = 12, session_nonce: str = "") -> None:
        """Initialize membrane potentials and precompute input currents for one sentence batch."""
        T, B, _ = X.shape
        n = self.shard.local_n
        current = (X.reshape(T * B, -1) @ self.shard.w_in.T).reshape(T, B, n)
        self._state = {
            "T": T,
            "B": B,
            "settle": settle,
            "t": 0,
            "nonce": session_nonce,
            "input_digest": _input_digest(X),
            "current": current,
            "v": np.zeros((B, n), np.float32),
            "a": np.zeros((B, n), np.float32),
            "z": np.zeros((B, n), np.float32),
            "ref": np.zeros((B, n), np.int16),
            "y": np.zeros((B, self.shard.w_out.shape[0]), np.float32),
            "zhat": np.zeros((B, n), np.float32),
            "total": np.zeros((B, n), np.float32),
            "charge": np.zeros((B, self.shard.w_out.shape[0]), np.float32),
            "plain_spikes": 0,
            "adaptive_spikes": 0,
            "spike_bytes_out": 0,
        }

    def step_exact(self, global_z_prev: np.ndarray) -> bytes:
        """Advance one millisecond tick in exact mode and return 1-bit packed local spikes."""
        st = self._state
        if st is None:
            raise RuntimeError("ShardNode.begin() must be called before step_exact()")
        s = self.shard
        d = s.dyn
        t = st["t"]
        z_local = st["z"]
        st["zhat"] = d.alpha * st["zhat"] + z_local
        rec_input = global_z_prev @ self._w_rec_t
        v = d.alpha * st["v"] + st["current"][t] + rec_input - d.v_th * z_local
        a = d.rho * st["a"] + z_local
        gap = v - (d.v_th + s.beta * a)
        quiet = st["ref"] > 0
        fired = (gap > 0) & ~quiet
        st["ref"] = np.where(fired, np.int16(d.refractory), np.maximum(st["ref"] - 1, 0)).astype(np.int16)
        z_new = fired.astype(np.float32)

        y_step = d.kappa * st["y"] + z_new @ self._w_out_t
        if s.b_out is not None:
            y_step = y_step + s.b_out

        st["v"] = v
        st["a"] = a
        st["z"] = z_new
        st["y"] = y_step
        st["total"] += z_new
        if t >= st["T"] - st["settle"]:
            st["charge"] += y_step

        plain_mask = s.beta == 0.0
        st["plain_spikes"] += int(z_new[:, plain_mask].sum())
        st["adaptive_spikes"] += int(z_new[:, ~plain_mask].sum())
        st["t"] = t + 1

        packed = pack_spikes(z_new)
        st["spike_bytes_out"] += len(packed)
        return packed

    def run_local_recurrent(self, X: np.ndarray, *, settle: int = 12, session_nonce: str = "") -> dict[str, Any]:
        """Run all T ticks locally (1-RTT WAN mode) and return a DID-signed contribution."""
        if self.shard.mode != LOCAL_RECURRENT:
            raise ValueError("run_local_recurrent requires shard.mode == 'local_recurrent'")
        self.begin(X, settle=settle, session_nonce=session_nonce)
        st = self._state
        assert st is not None
        for _ in range(st["T"]):
            self.step_exact(st["z"])
        return self.finalize()

    def finalize(self) -> dict[str, Any]:
        """Produce an Ed25519 DID-signed partial readout envelope for coordinator aggregation."""
        st = self._state
        if st is None:
            raise RuntimeError("ShardNode.begin() must be called before finalize()")
        s = self.shard
        T, settle = st["T"], st["settle"]
        partial_charge = (st["charge"] / float(settle)).astype(np.float32)
        total_rate = (st["total"] / float(T)).astype(np.float32)
        recent_rate = (st["zhat"] * (1.0 - s.dyn.alpha)).astype(np.float32)
        total_spikes = st["plain_spikes"] + st["adaptive_spikes"]

        payload = {
            "kind": "shard.contribution",
            "session_nonce": st["nonce"],
            "input_digest": st["input_digest"],
            "shard_id": s.shard_id,
            "n_shards": s.n_shards,
            "start": s.start,
            "end": s.end,
            "total_neurons": s.total_neurons,
            "mode": s.mode,
            "device_name": self.device_name,
            "role": self.role,
            "shard_digest": s.digest,
            "weight_bytes": s.weight_bytes,
            "batch": st["B"],
            "ticks": T,
            "outputs": int(s.w_out.shape[0]),
            "total_spikes": total_spikes,
            "plain_spikes": st["plain_spikes"],
            "adaptive_spikes": st["adaptive_spikes"],
            "spike_bytes_out": st["spike_bytes_out"],
            "partial_charge_b64": _encode_f32(partial_charge),
            "total_rate_b64": _encode_f32(total_rate),
            "recent_rate_b64": _encode_f32(recent_rate),
        }
        return sign_payload(self.identity, payload)


@dataclass(frozen=True)
class ShardedRunResult:
    """Aggregated result of a distributed N-device spiking cortex inference."""

    read: Read
    mode: str
    n_shards: int
    ticks: int
    rtt_rounds: int
    total_wire_bytes: int
    bytes_per_tick: float
    latency_ms: float
    all_signatures_valid: bool
    session_nonce: str
    nodes: tuple[dict[str, Any], ...]

    def telemetry(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "n_shards": self.n_shards,
            "ticks": self.ticks,
            "rtt_rounds": self.rtt_rounds,
            "total_wire_bytes": self.total_wire_bytes,
            "bytes_per_tick": round(self.bytes_per_tick, 1),
            "latency_ms": round(self.latency_ms, 3),
            "all_signatures_valid": self.all_signatures_valid,
            "session_nonce": self.session_nonce,
            "nodes": list(self.nodes),
        }


class ShardMesh:
    """Coordinator for an N-device distributed spiking prefrontal cortex."""

    def __init__(self, nodes: list[ShardNode], *, allowlist: Allowlist | None = None):
        if not nodes:
            raise ValueError("ShardMesh requires at least one ShardNode")
        self.nodes = sorted(nodes, key=lambda n: n.shard.shard_id)
        self.mode = self.nodes[0].shard.mode
        self.n_shards = self.nodes[0].shard.n_shards
        self.total_neurons = self.nodes[0].shard.total_neurons
        self.allowlist = allowlist if allowlist is not None else Allowlist([n.did for n in self.nodes])

    @classmethod
    def from_net(
        cls,
        net: Net,
        n_shards: int = 3,
        *,
        mode: str = EXACT,
        device_names: list[str] | None = None,
    ) -> ShardMesh:
        """Split a monolithic ``Net`` across ``n_shards`` freshly keyed Ed25519 device nodes."""
        shards = partition_net(net, n_shards, mode=mode)
        default_names = [
            "Desktop · Primary Host",
            "Phone · WebRTC Peer",
            "Laptop · LAN Node",
            "Home NAS · Always-On Node",
        ]
        roles = ["host", "mobile-peer", "lan-peer", "nas-peer"]
        nodes = []
        for i, s in enumerate(shards):
            name = (
                device_names[i]
                if device_names and i < len(device_names)
                else (default_names[i] if i < len(default_names) else f"Device #{i + 1}")
            )
            role = roles[i] if i < len(roles) else "peer"
            nodes.append(ShardNode(s, device_name=name, role=role))
        return cls(nodes)

    def aggregate(
        self,
        envelopes: list[dict[str, Any]],
        *,
        expected_nonce: str,
        expected_input_digest: str,
        batch: int,
        ticks: int,
    ) -> tuple[Read, list[dict[str, Any]], int]:
        """Verify all N Ed25519 shard envelopes and sum their partial charges (fail-closed)."""
        if len(envelopes) != self.n_shards:
            raise ShardVerificationError(
                f"incomplete shard quorum: got {len(envelopes)} of {self.n_shards} shards"
            )

        verified_by_id: dict[int, dict[str, Any]] = {}
        for env in envelopes:
            ok, signer = verify_payload(env, self.allowlist)
            if not ok or signer is None:
                raise ShardVerificationError(f"invalid or unauthorized shard signature (signer={signer!r})")
            if env.get("kind") != "shard.contribution":
                raise ShardVerificationError(f"unexpected envelope kind: {env.get('kind')!r}")
            if env.get("session_nonce") != expected_nonce:
                raise ShardVerificationError("shard session_nonce mismatch (possible replay)")
            if env.get("input_digest") != expected_input_digest:
                raise ShardVerificationError("shard input_digest mismatch (inconsistent prompt across nodes)")
            sid = int(env["shard_id"])
            if sid in verified_by_id:
                raise ShardVerificationError(f"duplicate shard_id {sid}")
            verified_by_id[sid] = env

        # Confirm complete, disjoint coverage [0 .. total_neurons)
        cursor = 0
        outputs = int(verified_by_id[0]["outputs"])
        charge = np.zeros((batch, outputs), np.float32)
        total_rate_full = np.zeros((batch, self.total_neurons), np.float32)
        recent_rate_full = np.zeros((batch, self.total_neurons), np.float32)
        total_spikes = 0
        envelope_bytes = 0
        node_views: list[dict[str, Any]] = []

        for sid in range(self.n_shards):
            if sid not in verified_by_id:
                raise ShardVerificationError(f"missing shard_id {sid}")
            env = verified_by_id[sid]
            start, end = int(env["start"]), int(env["end"])
            if start != cursor or end <= start:
                raise ShardVerificationError(f"non-contiguous shard range [{start}:{end}) expected start={cursor}")
            cursor = end
            local_n = end - start

            p_charge = _decode_f32(env["partial_charge_b64"], (batch, outputs))
            p_total = _decode_f32(env["total_rate_b64"], (batch, local_n))
            p_recent = _decode_f32(env["recent_rate_b64"], (batch, local_n))

            charge += p_charge
            total_rate_full[:, start:end] = p_total
            recent_rate_full[:, start:end] = p_recent
            total_spikes += int(env["total_spikes"])
            # Compact binary size of signed charge contribution (~outputs*4 + 64B Ed25519 sig)
            contrib_bytes = outputs * 4 + 64
            envelope_bytes += contrib_bytes
            node_views.append(
                {
                    "shard_id": sid,
                    "device_name": env["device_name"],
                    "role": env["role"],
                    "did": env["signer"],
                    "did_short": f"{env['signer'][:16]}...{env['signer'][-6:]}",
                    "neuron_range": [start, end],
                    "neurons": local_n,
                    "weight_kb": round(int(env["weight_bytes"]) / 1024.0, 1),
                    "total_spikes": int(env["total_spikes"]),
                    "plain_spikes": int(env["plain_spikes"]),
                    "adaptive_spikes": int(env["adaptive_spikes"]),
                    "wire_bytes": int(env["spike_bytes_out"]) + contrib_bytes,
                    "shard_digest": env["shard_digest"],
                    "signature_valid": True,
                }
            )

        if cursor != self.total_neurons:
            raise ShardVerificationError(f"incomplete neuron coverage: ended at {cursor}, expected {self.total_neurons}")

        pattern = np.concatenate([total_rate_full, recent_rate_full], axis=1)
        norm = np.linalg.norm(pattern, axis=1, keepdims=True)
        read = Read(
            charge=charge,
            pattern=pattern / np.maximum(norm, 1e-6),
            rate=float(total_spikes / max(1, ticks * batch * self.total_neurons)),
        )
        return read, node_views, envelope_bytes

    def run(self, X: np.ndarray, *, settle: int = 12) -> ShardedRunResult:
        """Execute one distributed sharded inference pass across all N devices."""
        t0 = time.perf_counter()
        T, B, _ = X.shape
        nonce = secrets.token_hex(12)
        in_digest = _input_digest(X)

        if self.mode == EXACT:
            for node in self.nodes:
                node.begin(X, settle=settle, session_nonce=nonce)
            global_z = np.zeros((B, self.total_neurons), np.float32)
            spike_wire_bytes = 0
            for _ in range(T):
                next_z = np.zeros((B, self.total_neurons), np.float32)
                for node in self.nodes:
                    packed = node.step_exact(global_z)
                    spike_wire_bytes += len(packed)
                    s = node.shard
                    next_z[:, s.start : s.end] = unpack_spikes(packed, batch=B, local_n=s.local_n)
                global_z = next_z
            envelopes = [node.finalize() for node in self.nodes]
            rtt_rounds = T + 1
        else:
            spike_wire_bytes = 0
            envelopes = [node.run_local_recurrent(X, settle=settle, session_nonce=nonce) for node in self.nodes]
            rtt_rounds = 1

        read, node_views, contrib_bytes = self.aggregate(
            envelopes,
            expected_nonce=nonce,
            expected_input_digest=in_digest,
            batch=B,
            ticks=T,
        )
        total_wire = spike_wire_bytes + contrib_bytes
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return ShardedRunResult(
            read=read,
            mode=self.mode,
            n_shards=self.n_shards,
            ticks=T,
            rtt_rounds=rtt_rounds,
            total_wire_bytes=total_wire,
            bytes_per_tick=spike_wire_bytes / max(1, T) if self.mode == EXACT else float(contrib_bytes),
            latency_ms=elapsed_ms,
            all_signatures_valid=True,
            session_nonce=nonce,
            nodes=tuple(node_views),
        )


__all__ = [
    "EXACT",
    "LOCAL_RECURRENT",
    "NetShard",
    "ShardMesh",
    "ShardNode",
    "ShardVerificationError",
    "ShardedRunResult",
    "pack_spikes",
    "partition_net",
    "unpack_spikes",
]
