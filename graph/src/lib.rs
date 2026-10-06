//! secdogie-graph: the state-graph core of the secdogie browser runtime.
//!
//! * [`canon`] -- canonical JSON with byte parity to the Python reference,
//!   including Python's float formatting and its int/float split;
//! * [`did`], [`envelope`] -- did:key and Ed25519 signed envelopes, verified here;
//! * [`delta`] -- `secdogie/state-graph-delta/v1`: content-addressed,
//!   append-only deltas (no removal, no tombstones);
//! * [`dag`] -- the DAG store and the grow-only topology view;
//! * [`sync`] -- have/want anti-entropy, transport-neutral;
//! * [`route`] -- the conservative lexical route adapter (candidate states only);
//! * [`plan`] -- deterministic action previews in the
//!   `secdogie/action-authorization/v1` field set;
//! * [`ffi`] -- the JSON surface, and its C ABI on wasm32.
//!
//! The crate has no network, clock, filesystem or randomness: it only judges
//! bytes it is handed. Built for wasm32 it has no imports at all.

pub mod canon;
pub mod dag;
pub mod delta;
pub mod did;
pub mod envelope;
pub mod ffi;
pub mod plan;
pub mod route;
pub mod sync;
pub mod url;
