//! The JSON-in / JSON-out surface the TypeScript runtime calls.
//!
//! Every request and response is canonical JSON text; responses are
//! `{"ok": true, ...}` or `{"ok": false, "error": "..."}` -- an error is always
//! a value, never a trap. The dispatch functions are plain safe Rust (and
//! tested natively); on `wasm32` a thin C ABI over linear memory exposes them:
//!
//! ```text
//! sd_alloc(len) -> ptr                 caller writes request bytes here
//! sd_free(ptr, len)                    frees a request or a response buffer
//! sd_graph_new(ptr, len) -> packed     {"trusted_authors": [did, ...], "max_nodes"?, "max_orphans"?}
//! sd_graph_call(handle, ptr, len) -> packed
//! sd_graph_drop(handle)
//! sd_route_scan(cfg_ptr, cfg_len, body_ptr, body_len) -> packed
//! sd_canonical(ptr, len) -> packed     {"ok": true, "canonical": text}
//! packed = (response_ptr << 32) | response_len
//! ```
//!
//! No wasm-bindgen: the boundary is small enough to keep by hand, and the
//! module has no imports at all -- it cannot reach the network, the DOM or the
//! clock.

use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;

use crate::canon::{self, Value, obj, str_arr};
use crate::dag::{Config, DagStore, Outcome};
use crate::delta;
use crate::plan::{self, Verb};
use crate::route::{self, ScanConfig};
use crate::sync;

thread_local! {
    static GRAPHS: RefCell<BTreeMap<u32, DagStore>> = const { RefCell::new(BTreeMap::new()) };
    static NEXT_HANDLE: Cell<u32> = const { Cell::new(1) };
}

fn ok<const N: usize>(pairs: [(&str, Value); N]) -> Value {
    let mut m: BTreeMap<String, Value> =
        pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
    m.insert("ok".into(), Value::Bool(true));
    Value::Obj(m)
}

fn fail(msg: impl Into<String>) -> Value {
    obj([
        ("ok", Value::Bool(false)),
        ("error", Value::Str(msg.into())),
    ])
}

fn strings(m: &BTreeMap<String, Value>, k: &str) -> Result<Vec<String>, String> {
    let Some(arr) = m.get(k).and_then(Value::as_arr) else {
        return Err(format!("{k} must be a list of strings"));
    };
    arr.iter()
        .map(|v| {
            v.as_str()
                .map(str::to_string)
                .ok_or_else(|| format!("{k} must be a list of strings"))
        })
        .collect()
}

fn string<'a>(m: &'a BTreeMap<String, Value>, k: &str) -> Result<&'a str, String> {
    m.get(k)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{k} must be a string"))
}

fn opt_usize(m: &BTreeMap<String, Value>, k: &str, default: usize) -> Result<usize, String> {
    match m.get(k) {
        None => Ok(default),
        Some(v) => v
            .as_u64()
            .map(|n| n as usize)
            .ok_or_else(|| format!("{k} must be an integer")),
    }
}

fn request(text: &str) -> Result<BTreeMap<String, Value>, String> {
    let lim = canon::Limits {
        max_bytes: 32 << 20,
        max_depth: 64,
    };
    match canon::parse_with(text, lim) {
        Ok(Value::Obj(m)) => Ok(m),
        Ok(_) => Err("request must be an object".into()),
        Err(e) => Err(format!("request is not valid JSON: {e}")),
    }
}

pub fn graph_new(req: &str) -> Value {
    let m = match request(req) {
        Ok(m) => m,
        Err(e) => return fail(e),
    };
    let build = || -> Result<DagStore, String> {
        let mut cfg = Config::new(strings(&m, "trusted_authors")?);
        cfg.max_nodes = opt_usize(&m, "max_nodes", cfg.max_nodes)?;
        cfg.max_orphans = opt_usize(&m, "max_orphans", cfg.max_orphans)?;
        DagStore::new(cfg).map_err(|e| e.0)
    };
    match build() {
        Ok(store) => {
            let h = NEXT_HANDLE.with(|n| {
                let h = n.get();
                n.set(h.wrapping_add(1).max(1));
                h
            });
            GRAPHS.with(|g| g.borrow_mut().insert(h, store));
            ok([("handle", Value::Int(h.to_string()))])
        }
        Err(e) => fail(e),
    }
}

pub fn graph_drop(handle: u32) {
    GRAPHS.with(|g| g.borrow_mut().remove(&handle));
}

fn outcome_value(o: &Outcome) -> Value {
    match o {
        Outcome::Inserted {
            cid,
            promoted,
            dropped,
        } => obj([
            ("outcome", Value::str("inserted")),
            ("cid", Value::str(cid)),
            ("promoted", str_arr(promoted.iter().cloned())),
            (
                "dropped",
                Value::Arr(
                    dropped
                        .iter()
                        .map(|(c, r)| obj([("cid", Value::str(c)), ("reason", Value::str(r))]))
                        .collect(),
                ),
            ),
        ]),
        Outcome::Duplicate { cid } => obj([
            ("outcome", Value::str("duplicate")),
            ("cid", Value::str(cid)),
        ]),
        Outcome::Pending { cid, missing } => obj([
            ("outcome", Value::str("pending")),
            ("cid", Value::str(cid)),
            ("missing", str_arr(missing.iter().cloned())),
        ]),
    }
}

fn call(store: &mut DagStore, m: &BTreeMap<String, Value>) -> Result<Value, String> {
    let op = string(m, "op")?;
    Ok(match op {
        "ingest" => match store.insert_wire(string(m, "wire")?) {
            Ok(o) => ok([("result", outcome_value(&o))]),
            Err(e) => fail(e.0),
        },
        "heads" => ok([
            ("heads", str_arr(store.heads())),
            ("count", Value::Int(store.len().to_string())),
        ]),
        "have" => ok([("message", sync::have_message(store))]),
        "on_have" => ok([(
            "want",
            sync::on_have(store, &strings(m, "heads")?).unwrap_or(Value::Null),
        )]),
        "on_want" => {
            let env = sync::on_want(store, &strings(m, "cids")?, &strings(m, "have_heads")?);
            ok([("message", sync::deltas_message(env))])
        }
        "on_deltas" => {
            let (rep, want) = sync::on_deltas(store, &strings(m, "envelopes")?);
            let rejected = rep
                .rejected
                .iter()
                .map(|(i, r)| {
                    obj([
                        ("index", Value::Int(i.to_string())),
                        ("reason", Value::str(r)),
                    ])
                })
                .collect();
            ok([
                (
                    "report",
                    obj([
                        ("inserted", str_arr(rep.inserted)),
                        ("duplicate", Value::Int(rep.duplicate.to_string())),
                        ("pending", str_arr(rep.pending)),
                        ("rejected", Value::Arr(rejected)),
                    ]),
                ),
                ("want", want.unwrap_or(Value::Null)),
            ])
        }
        "view" => ok([("view", store.view().to_value())]),
        "plan_action" => {
            let verb =
                Verb::parse(string(m, "verb")?).ok_or("verb must be \"navigate\" or \"submit\"")?;
            match plan::plan(store, string(m, "state_key")?, verb) {
                Ok(p) => ok([("plan", p.to_value())]),
                Err(e) => fail(e),
            }
        }
        other => return Err(format!("unknown op {other:?}")),
    })
}

pub fn graph_call(handle: u32, req: &str) -> Value {
    let m = match request(req) {
        Ok(m) => m,
        Err(e) => return fail(e),
    };
    // Stateless helpers need no handle.
    if let Some("state_key") = m.get("op").and_then(Value::as_str) {
        return match (
            string(&m, "origin"),
            string(&m, "route"),
            strings(&m, "query_keys"),
        ) {
            (Ok(o), Ok(r), Ok(q)) => ok([("key", Value::str(delta::state_key(o, r, &q)))]),
            _ => fail("state_key needs origin, route and query_keys"),
        };
    }
    GRAPHS.with(|g| match g.borrow_mut().get_mut(&handle) {
        None => fail("unknown graph handle"),
        Some(store) => call(store, &m).unwrap_or_else(fail),
    })
}

pub fn route_scan(cfg: &str, body: &[u8]) -> Value {
    let m = match request(cfg) {
        Ok(m) => m,
        Err(e) => return fail(e),
    };
    let build = || -> Result<ScanConfig, String> {
        let mut c = ScanConfig::new(string(&m, "document_url")?, strings(&m, "allowed_origins")?);
        c.max_bytes = opt_usize(&m, "max_bytes", c.max_bytes)?.min(route::DEFAULT_MAX_BYTES);
        c.max_candidates = opt_usize(&m, "max_candidates", c.max_candidates)?.min(4096);
        Ok(c)
    };
    let c = match build() {
        Ok(c) => c,
        Err(e) => return fail(e),
    };
    let text = String::from_utf8_lossy(body);
    match route::scan(&c, &text) {
        Ok(r) => ok([("scan", r.to_value())]),
        Err(e) => fail(e),
    }
}

pub fn canonical(text: &str) -> Value {
    match canon::parse(text) {
        Ok(v) => ok([("canonical", Value::Str(canon::to_string(&v)))]),
        Err(e) => fail(e.reason),
    }
}

#[cfg(target_arch = "wasm32")]
mod exports {
    use super::*;

    fn input<'a>(ptr: *const u8, len: usize) -> &'a [u8] {
        if len == 0 || ptr.is_null() {
            &[]
        } else {
            // SAFETY: the host wrote `len` bytes at `ptr` from `sd_alloc` and
            // keeps them alive for the duration of this call.
            unsafe { std::slice::from_raw_parts(ptr, len) }
        }
    }

    fn text<'a>(ptr: *const u8, len: usize) -> Result<&'a str, Value> {
        std::str::from_utf8(input(ptr, len)).map_err(|_| fail("request is not UTF-8"))
    }

    fn respond(v: Value) -> u64 {
        let buf = canon::to_bytes(&v).into_boxed_slice();
        let len = buf.len();
        let ptr = Box::into_raw(buf) as *mut u8;
        ((ptr as usize as u64) << 32) | len as u64
    }

    #[unsafe(no_mangle)]
    pub extern "C" fn sd_alloc(len: usize) -> *mut u8 {
        Box::into_raw(vec![0u8; len].into_boxed_slice()) as *mut u8
    }

    /// # Safety
    /// `ptr`/`len` must come from `sd_alloc` or a packed response, freed once.
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn sd_free(ptr: *mut u8, len: usize) {
        if !ptr.is_null() {
            // SAFETY: per the contract above, this is a boxed slice of `len` bytes.
            drop(unsafe { Box::from_raw(std::ptr::slice_from_raw_parts_mut(ptr, len)) });
        }
    }

    #[unsafe(no_mangle)]
    pub extern "C" fn sd_graph_new(ptr: *const u8, len: usize) -> u64 {
        respond(text(ptr, len).map(graph_new).unwrap_or_else(|e| e))
    }

    #[unsafe(no_mangle)]
    pub extern "C" fn sd_graph_call(handle: u32, ptr: *const u8, len: usize) -> u64 {
        respond(
            text(ptr, len)
                .map(|t| graph_call(handle, t))
                .unwrap_or_else(|e| e),
        )
    }

    #[unsafe(no_mangle)]
    pub extern "C" fn sd_graph_drop(handle: u32) {
        graph_drop(handle)
    }

    #[unsafe(no_mangle)]
    pub extern "C" fn sd_route_scan(
        cfg_ptr: *const u8,
        cfg_len: usize,
        body_ptr: *const u8,
        body_len: usize,
    ) -> u64 {
        respond(
            text(cfg_ptr, cfg_len)
                .map(|c| route_scan(c, input(body_ptr, body_len)))
                .unwrap_or_else(|e| e),
        )
    }

    #[unsafe(no_mangle)]
    pub extern "C" fn sd_canonical(ptr: *const u8, len: usize) -> u64 {
        respond(text(ptr, len).map(canonical).unwrap_or_else(|e| e))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dag::tests::{add_state, signed};
    use crate::envelope::testkit::Key;

    fn field<'a>(v: &'a Value, k: &str) -> &'a Value {
        &v.as_obj().unwrap()[k]
    }

    #[test]
    fn the_json_surface_round_trips() {
        let k = Key::from_label("agent");
        let created = graph_new(&format!(r#"{{"trusted_authors":["{}"]}}"#, k.did()));
        assert_eq!(field(&created, "ok"), &Value::Bool(true));
        let h: u32 = field(&created, "handle").as_u64().unwrap() as u32;

        let (a, wa) = signed(&k, &[], 1, vec![add_state("/a")]);
        let req = canon::to_string(&obj([
            ("op", Value::str("ingest")),
            ("wire", Value::str(&wa)),
        ]));
        let res = graph_call(h, &req);
        assert_eq!(field(field(&res, "result"), "cid"), &Value::str(&a));

        let heads = graph_call(h, r#"{"op":"heads"}"#);
        assert_eq!(field(&heads, "heads"), &str_arr([a.clone()]));

        let bad = graph_call(h, r#"{"op":"nope"}"#);
        assert_eq!(field(&bad, "ok"), &Value::Bool(false));
        assert_eq!(
            field(&graph_call(999, r#"{"op":"heads"}"#), "error"),
            &Value::str("unknown graph handle")
        );

        let key = graph_call(
            0,
            r#"{"op":"state_key","origin":"https://docs.example.com","route":"/a","query_keys":[]}"#,
        );
        assert_eq!(
            field(&key, "key"),
            &Value::str(delta::state_key("https://docs.example.com", "/a", &[]))
        );

        graph_drop(h);
        assert_eq!(
            field(&graph_call(h, r#"{"op":"heads"}"#), "ok"),
            &Value::Bool(false)
        );
    }

    #[test]
    fn zero_trust_is_reported_not_trapped() {
        let r = graph_new(r#"{"trusted_authors":[]}"#);
        assert_eq!(field(&r, "ok"), &Value::Bool(false));
        assert_eq!(field(&graph_new("not json"), "ok"), &Value::Bool(false));
    }

    #[test]
    fn route_scan_over_the_surface() {
        let r = route_scan(
            r#"{"document_url":"https://docs.example.com/","allowed_origins":["https://docs.example.com"]}"#,
            b"<a href=\"/x\">x</a>",
        );
        let scan = field(&r, "scan");
        assert_eq!(field(scan, "candidates").as_arr().unwrap().len(), 1);
        assert_eq!(
            field(&canonical("[1.0, 1e16]"), "canonical"),
            &Value::str("[1.0,1e+16]")
        );
    }
}
