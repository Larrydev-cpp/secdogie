use serde::Serialize;
use wasm_bindgen::prelude::*;

#[derive(Debug, Serialize)]
struct RouteEvidence {
    path: String,
    confidence: f32,
    kind: &'static str,
}

#[derive(Debug, Serialize)]
struct RouteGraph {
    routes: Vec<RouteEvidence>,
    transitions: Vec<(String, String)>,
}

fn extract_string_after(source: &str, marker: &str, start: usize) -> Option<(String, usize)> {
    let tail = &source[start..];
    let marker_at = tail.find(marker)? + start + marker.len();
    let after = &source[marker_at..];
    let quote_rel = after.find(['\'', '"', '`'])?;
    let quote = after.as_bytes()[quote_rel] as char;
    let body = &after[quote_rel + 1..];
    let end = body.find(quote)?;
    Some((body[..end].to_owned(), marker_at + quote_rel + end + 1))
}

fn lexical_routes(source: &str) -> Vec<RouteEvidence> {
    // This is deliberately a lexical adapter rather than a JavaScript parser.
    // A real AST engine can replace it without changing the WASM/TS boundary.
    let markers = [
        "path:", "route:", "href:", "to:", "push(", "replace(", "navigate(",
        "redirect(", "goto(",
    ];
    let mut out = Vec::new();
    for marker in markers {
        let mut cursor = 0usize;
        while let Some((value, next)) = extract_string_after(source, marker, cursor) {
            cursor = next;
            if value.starts_with('/') && !value.contains(' ') && !value.contains('#') && !value.contains('?') {
                out.push(RouteEvidence { path: value, confidence: 0.72, kind: "literal" });
            }
        }
    }
    out.sort_by(|a, b| a.path.cmp(&b.path));
    out.dedup_by(|a, b| a.path == b.path);
    out
}

#[wasm_bindgen]
pub fn reconstruct_public_routes(source: &str) -> Result<String, JsValue> {
    if source.len() > 4_000_000 {
        return Err(JsValue::from_str("source exceeds the WASM analysis budget"));
    }
    let routes = lexical_routes(source);
    // Route order is not transition evidence. The adapter therefore returns no
    // transitions; an AST-aware deployment should populate them only from an
    // explicit navigation edge (e.g. source location + target route).
    let graph = RouteGraph { routes, transitions: Vec::new() };
    serde_json::to_string(&graph).map_err(|e| JsValue::from_str(&e.to_string()))
}

#[wasm_bindgen]
pub fn adapter_name() -> String {
    "conservative-lexical-route-adapter/v1".into()
}
