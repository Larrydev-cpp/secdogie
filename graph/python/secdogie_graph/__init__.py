"""A thin ctypes binding to the native ``secdogie-graph`` cdylib.

The node's read-only web perception (``secdogie_agent.browse``) runs the same
conservative lexical route adapter the browser runs in wasm, so a page the core
fetches headless yields byte-identical candidate states. This module only loads
the library and marshals canonical JSON across the C ABI
(``sd_route_scan_c`` / ``sd_canonical_c`` / ``sd_string_free``); all the parsing
logic lives in Rust (``graph/src/route.rs``).

The library is built by ``cargo build --release`` in ``graph/`` and is *not*
bundled with this package. If it is missing we raise ``RouteAdapterUnavailable``
rather than silently degrading -- the web path simply reports itself
unavailable.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "DagStore",
    "RouteAdapterError",
    "RouteAdapterUnavailable",
    "canonical",
    "library_path",
    "route_scan",
]


class RouteAdapterError(RuntimeError):
    """The route adapter returned ``{"ok": false, ...}``."""


class RouteAdapterUnavailable(RuntimeError):
    """The native library is not built or cannot be loaded."""


def _lib_filename() -> str:
    if sys.platform == "darwin":
        return "libsecdogie_graph.dylib"
    if sys.platform in ("win32", "cygwin"):
        return "secdogie_graph.dll"
    return "libsecdogie_graph.so"


def _candidate_paths() -> list[Path]:
    env = os.environ.get("SECDOGIE_GRAPH_LIB")
    paths: list[Path] = []
    if env:
        paths.append(Path(env))
    name = _lib_filename()
    # graph/python/secdogie_graph/__init__.py -> graph/
    graph_root = Path(__file__).resolve().parents[2]
    for profile in ("release", "debug"):
        paths.append(graph_root / "target" / profile / name)
    return paths


def library_path() -> Path:
    """Where the native library is, or raise ``RouteAdapterUnavailable``."""
    tried: list[str] = []
    for p in _candidate_paths():
        tried.append(str(p))
        if p.is_file():
            return p
    raise RouteAdapterUnavailable(
        "the native route adapter is not built; run `cargo build --release` in "
        "graph/ (or set SECDOGIE_GRAPH_LIB). looked in: " + ", ".join(tried)
    )


class _Lib:
    """Loaded once, lazily."""

    _instance: _Lib | None = None

    def __init__(self) -> None:
        path = library_path()
        try:
            lib = ctypes.CDLL(str(path))
        except OSError as e:  # pragma: no cover - platform/link failures
            raise RouteAdapterUnavailable(f"cannot load {path}: {e}") from e
        lib.sd_route_scan_c.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        lib.sd_route_scan_c.restype = ctypes.c_void_p
        lib.sd_canonical_c.argtypes = [ctypes.c_char_p]
        lib.sd_canonical_c.restype = ctypes.c_void_p
        lib.sd_graph_new_c.argtypes = [ctypes.c_char_p]
        lib.sd_graph_new_c.restype = ctypes.c_void_p
        lib.sd_graph_call_c.argtypes = [ctypes.c_uint32, ctypes.c_char_p]
        lib.sd_graph_call_c.restype = ctypes.c_void_p
        lib.sd_graph_drop_c.argtypes = [ctypes.c_uint32]
        lib.sd_graph_drop_c.restype = None
        lib.sd_string_free.argtypes = [ctypes.c_void_p]
        lib.sd_string_free.restype = None
        self.lib = lib
        self.path = path

    @classmethod
    def get(cls) -> _Lib:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _take(self, ptr: int | None) -> dict[str, Any]:
        if not ptr:
            raise RouteAdapterUnavailable("the route adapter returned no result")
        try:
            raw = ctypes.cast(ptr, ctypes.c_char_p).value or b""
        finally:
            self.lib.sd_string_free(ptr)
        return json.loads(raw.decode("utf-8"))


def _unwrap(res: dict[str, Any]) -> dict[str, Any]:
    if not res.get("ok", False):
        raise RouteAdapterError(str(res.get("error", "route adapter error")))
    return res


def route_scan(
    document_url: str,
    body: bytes,
    *,
    allowed_origins: list[str] | None = None,
    max_bytes: int | None = None,
    max_candidates: int | None = None,
) -> dict[str, Any]:
    """Scan one fetched page body for the candidate states it names.

    Returns the adapter's ``scan`` object: ``document``, ``candidates`` (each
    with ``origin``/``route``/``query_keys``/``key``/``via``/``method``/
    ``fields``/``offset``), ``external``, ``rejected``, ``skipped_password_forms``
    and ``truncated``. Only candidates on ``allowed_origins`` are reported.
    """
    cfg: dict[str, Any] = {
        "document_url": document_url,
        "allowed_origins": list(allowed_origins or []),
    }
    if max_bytes is not None:
        cfg["max_bytes"] = max_bytes
    if max_candidates is not None:
        cfg["max_candidates"] = max_candidates
    lib = _Lib.get()
    ptr = lib.lib.sd_route_scan_c(json.dumps(cfg).encode("utf-8"), bytes(body), len(body))
    return _unwrap(lib._take(ptr))["scan"]


def canonical(text: str) -> str:
    """Canonicalise JSON ``text`` the way the Rust/TS core does (parity checks)."""
    lib = _Lib.get()
    ptr = lib.lib.sd_canonical_c(text.encode("utf-8"))
    return _unwrap(lib._take(ptr))["canonical"]


class DagStore:
    """The content-addressed, append-only topology DAG, backed by the native
    crate. Zero-trust: ``trusted_authors`` must be non-empty. Deltas are signed
    envelopes (same format as ``secdogie_identity.sign_payload``); only ops that
    *add* exist, so the store converges regardless of delivery order."""

    def __init__(
        self,
        trusted_authors: list[str],
        *,
        max_nodes: int | None = None,
        max_orphans: int | None = None,
    ) -> None:
        cfg: dict[str, Any] = {"trusted_authors": list(trusted_authors)}
        if max_nodes is not None:
            cfg["max_nodes"] = max_nodes
        if max_orphans is not None:
            cfg["max_orphans"] = max_orphans
        self._lib = _Lib.get()
        res = _unwrap(self._lib._take(self._lib.lib.sd_graph_new_c(json.dumps(cfg).encode("utf-8"))))
        self._handle = int(res["handle"])
        self._open = True

    def _call(self, req: dict[str, Any]) -> dict[str, Any]:
        if not self._open:
            raise RouteAdapterError("DagStore is closed")
        ptr = self._lib.lib.sd_graph_call_c(self._handle, json.dumps(req).encode("utf-8"))
        return _unwrap(self._lib._take(ptr))

    def ingest(self, wire: str) -> dict[str, Any]:
        """Ingest one signed delta (canonical JSON text). Returns the outcome
        (``inserted`` / ``duplicate`` / ``pending``)."""
        return self._call({"op": "ingest", "wire": wire})["result"]

    def heads(self) -> dict[str, Any]:
        r = self._call({"op": "heads"})
        return {"heads": r["heads"], "count": int(r["count"]), "next_lamport": int(r["next_lamport"])}

    def view(self) -> dict[str, Any]:
        """The materialized topology (states, references, observations)."""
        return self._call({"op": "view"})["view"]

    def close(self) -> None:
        if getattr(self, "_open", False):
            self._lib.lib.sd_graph_drop_c(self._handle)
            self._open = False

    def __enter__(self) -> DagStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # best-effort; handles are freed on close/GC
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass
