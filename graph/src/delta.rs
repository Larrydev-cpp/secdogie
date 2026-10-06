//! `secdogie/state-graph-delta/v1`: one signed, content-addressed node of the
//! topology DAG.
//!
//! ```text
//! payload = {"type": "secdogie/state-graph-delta/v1",
//!            "author": did, "parents": [cid, ...], "lamport": int, "ops": [op, ...]}
//! envelope = payload + {"signer": did, "sig": b64}      (signer == author)
//! cid = sha256(canonical(payload)).hex()                 (like journal._entry_hash)
//! ```
//!
//! **Append-only.** v1 has exactly three ops and all of them add:
//! `add_state`, `add_reference`, `add_observation`. There is no remove, no
//! tombstone, no overwrite -- an op with any other name is refused, so the
//! materialized topology is a grow-only set union and converges regardless of
//! delivery order without deletion semantics.
//!
//! **No floats.** Every number in a delta is an integer, so the Python float
//! formatting rules (see `canon`) never touch a content address.
//!
//! **Candidate states, not transitions.** An `add_reference` records that a
//! page's markup *refers* to another state (a link, a form action). It does not
//! claim the reference was ever followed, and no op can.
//!
//! Parsing is strict: every field is required, unknown fields are refused,
//! types are exact (an integral float is not an integer), lists that act as
//! sets must be sorted and unique so one value has exactly one encoding.

use std::collections::BTreeMap;

use sha2::{Digest, Sha256};

use crate::canon::{self, Value, obj, str_arr};
use crate::url::{is_origin, is_query_key, is_route};

pub const DELTA_TYPE: &str = "secdogie/state-graph-delta/v1";
pub const STATE_KEY_TYPE: &str = "secdogie/state-key/v1";

pub const MAX_PARENTS: usize = 16;
pub const MAX_OPS: usize = 256;
pub const MAX_FIELDS: usize = 64;
pub const MAX_FIELD_LEN: usize = 128;
/// Lamport clocks and byte lengths stay within 2**53 so every runtime holds them exactly.
pub const MAX_SAFE_INT: u64 = (1 << 53) - 1;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Via {
    A,
    Area,
    Form,
    Link,
    Redirect,
}

impl Via {
    pub fn as_str(self) -> &'static str {
        match self {
            Via::A => "a",
            Via::Area => "area",
            Via::Form => "form",
            Via::Link => "link",
            Via::Redirect => "redirect",
        }
    }

    pub fn parse(s: &str) -> Option<Via> {
        Some(match s {
            "a" => Via::A,
            "area" => Via::Area,
            "form" => Via::Form,
            "link" => Via::Link,
            "redirect" => Via::Redirect,
            _ => return None,
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Method {
    Get,
    Post,
}

impl Method {
    pub fn as_str(self) -> &'static str {
        match self {
            Method::Get => "get",
            Method::Post => "post",
        }
    }

    pub fn parse(s: &str) -> Option<Method> {
        match s {
            "get" => Some(Method::Get),
            "post" => Some(Method::Post),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Reference {
    pub from: String,
    pub to: String,
    pub via: Via,
    pub method: Method,
    pub fields: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Op {
    AddState {
        origin: String,
        route: String,
        query_keys: Vec<String>,
    },
    AddReference(Reference),
    AddObservation {
        state: String,
        content_hash: String,
        byte_len: u64,
    },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GraphDelta {
    pub author: String,
    pub parents: Vec<String>,
    pub lamport: u64,
    pub ops: Vec<Op>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DeltaError(pub String);

impl std::fmt::Display for DeltaError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

fn err<T>(msg: impl Into<String>) -> Result<T, DeltaError> {
    Err(DeltaError(msg.into()))
}

pub fn is_hex64(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

pub fn sha256_hex(data: &[u8]) -> String {
    let d = Sha256::digest(data);
    let mut out = String::with_capacity(64);
    for b in d {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

/// The content address of a payload.
pub fn cid_of(payload: &Value) -> String {
    sha256_hex(&canon::to_bytes(payload))
}

/// The stable key of a state: what identifies "this route on this origin with
/// these query parameter names", independent of who saw it or when.
pub fn state_key(origin: &str, route: &str, query_keys: &[String]) -> String {
    let v = obj([
        ("type", Value::str(STATE_KEY_TYPE)),
        ("origin", Value::str(origin)),
        ("route", Value::str(route)),
        ("query_keys", str_arr(query_keys.iter().cloned())),
    ]);
    cid_of(&v)
}

fn fields_exactly<'a>(
    v: &'a Value,
    names: &[&str],
    what: &str,
) -> Result<&'a BTreeMap<String, Value>, DeltaError> {
    let Some(m) = v.as_obj() else {
        return err(format!("{what} must be an object"));
    };
    if let Some(k) = m.keys().find(|k| !names.contains(&k.as_str())) {
        return err(format!("{what}: unknown field {k:?}"));
    }
    if let Some(k) = names.iter().find(|k| !m.contains_key(**k)) {
        return err(format!("{what}: missing field {k:?}"));
    }
    Ok(m)
}

fn get_str<'a>(m: &'a BTreeMap<String, Value>, k: &str, what: &str) -> Result<&'a str, DeltaError> {
    match m.get(k) {
        Some(Value::Str(s)) => Ok(s),
        _ => err(format!("{what}.{k} must be a string")),
    }
}

fn get_int(m: &BTreeMap<String, Value>, k: &str, what: &str) -> Result<u64, DeltaError> {
    match m.get(k) {
        Some(Value::Int(_)) => match m.get(k).and_then(Value::as_u64) {
            Some(n) if n <= MAX_SAFE_INT => Ok(n),
            _ => err(format!("{what}.{k} is out of range")),
        },
        _ => err(format!("{what}.{k} must be an integer")),
    }
}

/// A list of strings that must be sorted ascending and unique.
fn get_sorted_strs(
    m: &BTreeMap<String, Value>,
    k: &str,
    what: &str,
    max: usize,
    each: impl Fn(&str) -> bool,
) -> Result<Vec<String>, DeltaError> {
    let Some(items) = m.get(k).and_then(Value::as_arr) else {
        return err(format!("{what}.{k} must be a list"));
    };
    if items.len() > max {
        return err(format!("{what}.{k} has more than {max} items"));
    }
    let mut out: Vec<String> = Vec::with_capacity(items.len());
    for it in items {
        let Some(s) = it.as_str() else {
            return err(format!("{what}.{k} must contain strings"));
        };
        if !each(s) {
            return err(format!("{what}.{k} contains an invalid entry {s:?}"));
        }
        if out.last().is_some_and(|prev| prev.as_str() >= s) {
            return err(format!("{what}.{k} must be sorted and unique"));
        }
        out.push(s.to_string());
    }
    Ok(out)
}

fn is_field_name(s: &str) -> bool {
    !s.is_empty() && s.len() <= MAX_FIELD_LEN && !s.chars().any(char::is_control)
}

fn parse_op(v: &Value) -> Result<Op, DeltaError> {
    let Some(name) = v.as_obj().and_then(|m| m.get("op")).and_then(Value::as_str) else {
        return err("op must be an object with an \"op\" name");
    };
    match name {
        "add_state" => {
            let m = fields_exactly(v, &["op", "origin", "route", "query_keys"], "add_state")?;
            let origin = get_str(m, "origin", "add_state")?;
            if !is_origin(origin) {
                return err(format!(
                    "add_state.origin must be an https origin, got {origin:?}"
                ));
            }
            let route = get_str(m, "route", "add_state")?;
            if !is_route(route) {
                return err(format!(
                    "add_state.route must be a normalized path, got {route:?}"
                ));
            }
            let query_keys = get_sorted_strs(m, "query_keys", "add_state", 64, is_query_key)?;
            Ok(Op::AddState {
                origin: origin.into(),
                route: route.into(),
                query_keys,
            })
        }
        "add_reference" => {
            let m = fields_exactly(
                v,
                &["op", "from", "to", "via", "method", "fields"],
                "add_reference",
            )?;
            let from = get_str(m, "from", "add_reference")?;
            let to = get_str(m, "to", "add_reference")?;
            if !is_hex64(from) || !is_hex64(to) {
                return err("add_reference.from/to must be state keys");
            }
            let Some(via) = Via::parse(get_str(m, "via", "add_reference")?) else {
                return err("add_reference.via is not a known reference kind");
            };
            let Some(method) = Method::parse(get_str(m, "method", "add_reference")?) else {
                return err("add_reference.method must be \"get\" or \"post\"");
            };
            let fields = get_sorted_strs(m, "fields", "add_reference", MAX_FIELDS, is_field_name)?;
            if via != Via::Form && (method != Method::Get || !fields.is_empty()) {
                return err("only a form reference carries a method other than get, or fields");
            }
            Ok(Op::AddReference(Reference {
                from: from.into(),
                to: to.into(),
                via,
                method,
                fields,
            }))
        }
        "add_observation" => {
            let m = fields_exactly(
                v,
                &["op", "state", "content_hash", "byte_len"],
                "add_observation",
            )?;
            let state = get_str(m, "state", "add_observation")?;
            let content_hash = get_str(m, "content_hash", "add_observation")?;
            if !is_hex64(state) || !is_hex64(content_hash) {
                return err("add_observation.state/content_hash must be sha256 hex digests");
            }
            let byte_len = get_int(m, "byte_len", "add_observation")?;
            Ok(Op::AddObservation {
                state: state.into(),
                content_hash: content_hash.into(),
                byte_len,
            })
        }
        other => err(format!(
            "unknown op {other:?}: v1 deltas only add (no removal, no tombstones)"
        )),
    }
}

/// Parses and validates a delta payload (the envelope minus `signer`/`sig`).
pub fn parse_payload(v: &Value) -> Result<GraphDelta, DeltaError> {
    let m = fields_exactly(v, &["type", "author", "parents", "lamport", "ops"], "delta")?;
    if get_str(m, "type", "delta")? != DELTA_TYPE {
        return err(format!(
            "delta.type must be {DELTA_TYPE:?} (domain separation)"
        ));
    }
    let author = get_str(m, "author", "delta")?.to_string();
    let parents = get_sorted_strs(m, "parents", "delta", MAX_PARENTS, is_hex64).map_err(|e| {
        DeltaError(format!(
            "parents must be sorted, unique sha256 hex digests ({e})"
        ))
    })?;
    let lamport = match m.get("lamport") {
        Some(Value::Int(_)) => get_int(m, "lamport", "delta")?,
        _ => return err("lamport must be an integer"),
    };
    if lamport == 0 {
        return err("lamport must be at least 1");
    }
    let Some(raw_ops) = m.get("ops").and_then(Value::as_arr) else {
        return err("delta.ops must be a list");
    };
    if raw_ops.is_empty() || raw_ops.len() > MAX_OPS {
        return err(format!("delta.ops must hold 1..={MAX_OPS} ops"));
    }
    let ops = raw_ops
        .iter()
        .map(parse_op)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(GraphDelta {
        author,
        parents,
        lamport,
        ops,
    })
}

fn op_value(op: &Op) -> Value {
    match op {
        Op::AddState {
            origin,
            route,
            query_keys,
        } => obj([
            ("op", Value::str("add_state")),
            ("origin", Value::str(origin)),
            ("route", Value::str(route)),
            ("query_keys", str_arr(query_keys.iter().cloned())),
        ]),
        Op::AddReference(r) => obj([
            ("op", Value::str("add_reference")),
            ("from", Value::str(&r.from)),
            ("to", Value::str(&r.to)),
            ("via", Value::str(r.via.as_str())),
            ("method", Value::str(r.method.as_str())),
            ("fields", str_arr(r.fields.iter().cloned())),
        ]),
        Op::AddObservation {
            state,
            content_hash,
            byte_len,
        } => obj([
            ("op", Value::str("add_observation")),
            ("state", Value::str(state)),
            ("content_hash", Value::str(content_hash)),
            ("byte_len", Value::Int(byte_len.to_string())),
        ]),
    }
}

/// The payload value of a delta (inverse of `parse_payload`).
pub fn payload_value(d: &GraphDelta) -> Value {
    obj([
        ("type", Value::str(DELTA_TYPE)),
        ("author", Value::str(&d.author)),
        ("parents", str_arr(d.parents.iter().cloned())),
        ("lamport", Value::Int(d.lamport.to_string())),
        ("ops", Value::Arr(d.ops.iter().map(op_value).collect())),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::canon::parse;

    fn root(ops: &str) -> String {
        format!(
            r#"{{"type":"{DELTA_TYPE}","author":"did:key:z6Mk","parents":[],"lamport":1,"ops":[{ops}]}}"#
        )
    }

    const STATE: &str = r#"{"op":"add_state","origin":"https://docs.example.com","route":"/a","query_keys":["lang"]}"#;

    #[test]
    fn round_trips_through_its_value() {
        let v = parse(&root(STATE)).unwrap();
        let d = parse_payload(&v).unwrap();
        assert_eq!(payload_value(&d), v);
    }

    #[test]
    fn only_additions_exist() {
        for op in [
            "remove_state",
            "tombstone",
            "delete",
            "set_state",
            "add_transition",
        ] {
            let e =
                parse_payload(&parse(&root(&format!(r#"{{"op":"{op}","state":"x"}}"#))).unwrap())
                    .unwrap_err();
            assert!(e.0.starts_with("unknown op"), "{op}: {e}");
        }
    }

    #[test]
    fn strict_schema() {
        let bad = [
            (
                root(&STATE.replace("https://docs", "http://docs")),
                "origin",
            ),
            (root(&STATE.replace("/a", "/x/../a")), "route"),
            (
                root(&STATE.replace(r#"["lang"]"#, r#"["z","a"]"#)),
                "sorted",
            ),
            (
                root(&STATE.replace(r#""query_keys""#, r#""extra":1,"query_keys""#)),
                "unknown field",
            ),
            (
                root(STATE).replace(r#""lamport":1"#, r#""lamport":1.0"#),
                "lamport must be an integer",
            ),
            (
                root(STATE).replace(r#""lamport":1"#, r#""lamport":0"#),
                "at least 1",
            ),
            (root(""), "1..="),
            (
                root(&format!(
                    r#"{{"op":"add_reference","from":"{a}","to":"{a}","via":"a","method":"post","fields":[]}}"#,
                    a = "a".repeat(64)
                )),
                "only a form",
            ),
        ];
        for (text, want) in bad {
            let e = parse_payload(&parse(&text).unwrap()).unwrap_err();
            assert!(e.0.contains(want), "{want}: {e}");
        }
    }
}
