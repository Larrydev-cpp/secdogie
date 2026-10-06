//! The conservative lexical route adapter.
//!
//! Given the (already bounded) body of one fetched page, it reports the
//! **candidate states** the markup literally refers to -- and nothing else:
//!
//! * `<a href>`, `<area href>`;
//! * `<link href rel=canonical|alternate|next|prev>`;
//! * `<form action method>`, with the *names* of its fields (never values). A
//!   form with a password field is skipped entirely;
//! * the first `<base href>`, honoured only when it stays on an allowed origin.
//!
//! It never executes or interprets script, never reads `on*` handlers,
//! `data-*`, `srcset` or inline JS for URLs, and skips the contents of
//! `script`, `style`, `template`, `textarea`, `noscript` and the other raw-text
//! elements. So it never *invents* a transition: a candidate means "this page's
//! markup names that state", not "following it leads there" and not "it was
//! followed". Turning a candidate into an observed state is a separate, bounded
//! fetch of that URL.
//!
//! Only candidates on an allowed origin are reported; others are only counted.
//! URLs it will not interpret (other schemes, userinfo, IDN, IPv6 literals,
//! backslashes) are counted by reason, never repaired.

use std::collections::{BTreeMap, BTreeSet};

use crate::canon::{Value, obj, str_arr};
use crate::delta::{Method, Via, state_key};
use crate::url::{HttpsUrl, is_origin, parse_https, resolve};

pub const DEFAULT_MAX_BYTES: usize = 8 << 20;
pub const DEFAULT_MAX_CANDIDATES: usize = 512;

const RAW_TEXT: [&str; 10] = [
    "script", "style", "template", "textarea", "noscript", "xmp", "iframe", "noembed", "noframes",
    "title",
];
const LINK_RELS: [&str; 4] = ["canonical", "alternate", "next", "prev"];

#[derive(Clone, Debug)]
pub struct ScanConfig {
    pub document_url: String,
    pub allowed_origins: Vec<String>,
    pub max_bytes: usize,
    pub max_candidates: usize,
}

impl ScanConfig {
    pub fn new(document_url: impl Into<String>, allowed_origins: Vec<String>) -> ScanConfig {
        ScanConfig {
            document_url: document_url.into(),
            allowed_origins,
            max_bytes: DEFAULT_MAX_BYTES,
            max_candidates: DEFAULT_MAX_CANDIDATES,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct CandidateState {
    pub origin: String,
    pub route: String,
    pub query_keys: Vec<String>,
    pub via: Via,
    pub method: Method,
    pub fields: Vec<String>,
    /// Byte offset of the tag that named it, as evidence.
    pub offset: usize,
}

impl CandidateState {
    pub fn key(&self) -> String {
        state_key(&self.origin, &self.route, &self.query_keys)
    }
}

#[derive(Clone, Debug, Default)]
pub struct ScanResult {
    pub document: Option<(String, String, Vec<String>)>,
    pub candidates: Vec<CandidateState>,
    pub external: usize,
    pub rejected: BTreeMap<&'static str, usize>,
    pub skipped_password_forms: usize,
    pub truncated: bool,
}

impl ScanResult {
    pub fn to_value(&self) -> Value {
        let doc = match &self.document {
            Some((o, r, q)) => obj([
                ("origin", Value::str(o)),
                ("route", Value::str(r)),
                ("query_keys", str_arr(q.iter().cloned())),
                ("key", Value::str(state_key(o, r, q))),
            ]),
            None => Value::Null,
        };
        let cands = self
            .candidates
            .iter()
            .map(|c| {
                obj([
                    ("origin", Value::str(&c.origin)),
                    ("route", Value::str(&c.route)),
                    ("query_keys", str_arr(c.query_keys.iter().cloned())),
                    ("key", Value::str(c.key())),
                    ("via", Value::str(c.via.as_str())),
                    ("method", Value::str(c.method.as_str())),
                    ("fields", str_arr(c.fields.iter().cloned())),
                    ("offset", Value::Int(c.offset.to_string())),
                ])
            })
            .collect();
        obj([
            ("document", doc),
            ("candidates", Value::Arr(cands)),
            ("external", Value::Int(self.external.to_string())),
            (
                "rejected",
                Value::Obj(
                    self.rejected
                        .iter()
                        .map(|(k, n)| (k.to_string(), Value::Int(n.to_string())))
                        .collect(),
                ),
            ),
            (
                "skipped_password_forms",
                Value::Int(self.skipped_password_forms.to_string()),
            ),
            ("truncated", Value::Bool(self.truncated)),
        ])
    }
}

struct Tag {
    name: String,
    end: bool,
    attrs: Vec<(String, String)>,
    offset: usize,
}

impl Tag {
    fn attr(&self, name: &str) -> Option<&str> {
        self.attrs
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.as_str())
    }
}

fn find(hay: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if from >= hay.len() {
        return None;
    }
    hay[from..]
        .windows(needle.len())
        .position(|w| w == needle)
        .map(|p| p + from)
}

fn find_ci(hay: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if from >= hay.len() {
        return None;
    }
    hay[from..]
        .windows(needle.len())
        .position(|w| w.eq_ignore_ascii_case(needle))
        .map(|p| p + from)
}

fn is_ws(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r' | b'\x0c')
}

/// Decodes the character references an attribute value commonly carries.
/// Unknown named references are left as written.
fn decode_entities(s: &str) -> String {
    if !s.contains('&') {
        return s.to_string();
    }
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(p) = rest.find('&') {
        out.push_str(&rest[..p]);
        rest = &rest[p..];
        let Some(semi) = rest[..rest.len().min(12)].find(';') else {
            out.push('&');
            rest = &rest[1..];
            continue;
        };
        let ent = &rest[1..semi];
        let decoded = match ent {
            "amp" => Some('&'),
            "lt" => Some('<'),
            "gt" => Some('>'),
            "quot" => Some('"'),
            "apos" => Some('\''),
            _ if ent.starts_with("#x") || ent.starts_with("#X") => {
                u32::from_str_radix(&ent[2..], 16)
                    .ok()
                    .map(|n| char::from_u32(n).unwrap_or('\u{fffd}'))
            }
            _ if ent.starts_with('#') => ent[1..]
                .parse::<u32>()
                .ok()
                .map(|n| char::from_u32(n).unwrap_or('\u{fffd}')),
            _ => None,
        };
        match decoded {
            Some('\0') => {
                out.push('\u{fffd}');
                rest = &rest[semi + 1..];
            }
            Some(c) => {
                out.push(c);
                rest = &rest[semi + 1..];
            }
            None => {
                out.push('&');
                rest = &rest[1..];
            }
        }
    }
    out.push_str(rest);
    out
}

/// Walks the tags of `s`, skipping comments, doctypes, processing
/// instructions and the contents of raw-text elements.
fn for_each_tag(s: &str, mut f: impl FnMut(Tag) -> bool) {
    let b = s.as_bytes();
    let n = b.len();
    let mut i = 0;
    while i < n {
        let Some(lt) = b[i..].iter().position(|&c| c == b'<').map(|p| p + i) else {
            return;
        };
        i = lt;
        if b[i..].starts_with(b"<!--") {
            i = find(b, b"-->", i + 4).map_or(n, |j| j + 3);
            continue;
        }
        if b[i..].starts_with(b"<!") || b[i..].starts_with(b"<?") {
            i = find(b, b">", i).map_or(n, |j| j + 1);
            continue;
        }
        let end = b.get(i + 1) == Some(&b'/');
        let name_start = if end { i + 2 } else { i + 1 };
        if !b.get(name_start).is_some_and(u8::is_ascii_alphabetic) {
            i += 1;
            continue;
        }
        let mut j = name_start;
        while j < n && (b[j].is_ascii_alphanumeric() || b[j] == b'-' || b[j] == b':') {
            j += 1;
        }
        let name = s[name_start..j].to_ascii_lowercase();
        let mut attrs = Vec::new();
        loop {
            while j < n && (is_ws(b[j]) || b[j] == b'/') {
                j += 1;
            }
            if j >= n {
                break;
            }
            if b[j] == b'>' {
                j += 1;
                break;
            }
            let an = j;
            while j < n && !is_ws(b[j]) && !matches!(b[j], b'=' | b'>' | b'/') {
                j += 1;
            }
            if j == an {
                j += 1; // a lone '=' -- not an attribute name; move on
                continue;
            }
            let aname = s[an..j].to_ascii_lowercase();
            while j < n && is_ws(b[j]) {
                j += 1;
            }
            let mut value = String::new();
            if j < n && b[j] == b'=' {
                j += 1;
                while j < n && is_ws(b[j]) {
                    j += 1;
                }
                if j < n && (b[j] == b'"' || b[j] == b'\'') {
                    let q = b[j];
                    let vs = j + 1;
                    let ve = b[vs..].iter().position(|&c| c == q).map_or(n, |p| p + vs);
                    value = s[vs..ve].to_string();
                    j = (ve + 1).min(n);
                } else {
                    let vs = j;
                    while j < n && !is_ws(b[j]) && b[j] != b'>' {
                        j += 1;
                    }
                    value = s[vs..j].to_string();
                }
            }
            attrs.push((aname, decode_entities(&value)));
        }
        i = j;
        if !end && name == "plaintext" {
            return;
        }
        let raw = !end && RAW_TEXT.contains(&name.as_str());
        let close = format!("</{name}");
        if !f(Tag {
            name,
            end,
            attrs,
            offset: lt,
        }) {
            return;
        }
        if raw {
            i = find_ci(b, close.as_bytes(), i).unwrap_or(n);
        }
    }
}

struct FormAcc {
    action: Result<Option<HttpsUrl>, crate::url::Reject>,
    method: Method,
    fields: BTreeSet<String>,
    has_password: bool,
    offset: usize,
}

/// What makes two candidates the same: everything but the evidence offset.
type CandidateIdentity = (String, String, Vec<String>, Via, Method, Vec<String>);

struct Scanner<'a> {
    allowed: &'a BTreeSet<String>,
    max: usize,
    out: ScanResult,
    seen: BTreeSet<CandidateIdentity>,
}

impl Scanner<'_> {
    fn emit(
        &mut self,
        target: Result<Option<HttpsUrl>, crate::url::Reject>,
        via: Via,
        method: Method,
        fields: Vec<String>,
        offset: usize,
    ) {
        let u = match target {
            Err(r) => {
                *self.out.rejected.entry(r.label()).or_default() += 1;
                return;
            }
            Ok(None) => return, // the same document
            Ok(Some(u)) => u,
        };
        let origin = u.origin();
        if !self.allowed.contains(&origin) {
            self.out.external += 1;
            return;
        }
        let query_keys = u.query_keys();
        let k = (
            origin.clone(),
            u.path.clone(),
            query_keys.clone(),
            via,
            method,
            fields.clone(),
        );
        if self.seen.contains(&k) {
            return;
        }
        if self.out.candidates.len() >= self.max {
            self.out.truncated = true;
            return;
        }
        self.seen.insert(k);
        self.out.candidates.push(CandidateState {
            origin,
            route: u.path,
            query_keys,
            via,
            method,
            fields,
            offset,
        });
    }

    fn close_form(&mut self, f: FormAcc) {
        if f.has_password {
            self.out.skipped_password_forms += 1;
            return;
        }
        self.emit(
            f.action,
            Via::Form,
            f.method,
            f.fields.into_iter().collect(),
            f.offset,
        );
    }
}

/// Scans `body` as the page at `cfg.document_url`.
pub fn scan(cfg: &ScanConfig, body: &str) -> Result<ScanResult, String> {
    let mut allowed = BTreeSet::new();
    for o in &cfg.allowed_origins {
        if !is_origin(o) {
            return Err(format!(
                "allowed origin {o:?} is not a canonical https origin"
            ));
        }
        allowed.insert(o.clone());
    }
    if allowed.is_empty() {
        return Err(
            "no allowed origins: the adapter reports nothing without an explicit allowlist".into(),
        );
    }
    let doc = parse_https(&cfg.document_url)
        .map_err(|r| format!("document URL refused ({})", r.label()))?;
    if !allowed.contains(&doc.origin()) {
        return Err("document origin is not on the allowlist".into());
    }
    let mut body = body;
    let mut truncated = false;
    if body.len() > cfg.max_bytes {
        let mut cut = cfg.max_bytes;
        while !body.is_char_boundary(cut) {
            cut -= 1;
        }
        body = &body[..cut];
        truncated = true;
    }

    let mut sc = Scanner {
        allowed: &allowed,
        max: cfg.max_candidates,
        out: ScanResult::default(),
        seen: BTreeSet::new(),
    };
    sc.out.document = Some((doc.origin(), doc.path.clone(), doc.query_keys()));
    let mut base = doc.clone();
    let mut base_seen = false;
    let mut form: Option<FormAcc> = None;

    for_each_tag(body, |t| {
        match (t.name.as_str(), t.end) {
            ("base", false) if !base_seen => {
                if let Some(href) = t.attr("href") {
                    base_seen = true;
                    match resolve(&doc, href) {
                        Ok(Some(u)) if allowed.contains(&u.origin()) => base = u,
                        _ => *sc.out.rejected.entry("base").or_default() += 1,
                    }
                }
            }
            ("a" | "area", false) => {
                if let Some(href) = t.attr("href") {
                    let via = if t.name == "a" { Via::A } else { Via::Area };
                    sc.emit(resolve(&base, href), via, Method::Get, vec![], t.offset);
                }
            }
            ("link", false) => {
                let rel = t.attr("rel").unwrap_or("").to_ascii_lowercase();
                if rel.split_ascii_whitespace().any(|r| LINK_RELS.contains(&r)) {
                    if let Some(href) = t.attr("href") {
                        sc.emit(
                            resolve(&base, href),
                            Via::Link,
                            Method::Get,
                            vec![],
                            t.offset,
                        );
                    }
                }
            }
            ("form", false) if form.is_none() => {
                let method = t
                    .attr("method")
                    .unwrap_or("get")
                    .trim()
                    .to_ascii_lowercase();
                if method == "dialog" {
                    return true; // closes a dialog; not a navigation
                }
                let action = match t.attr("action").map(str::trim) {
                    None | Some("") => Ok(Some(doc.clone())),
                    Some(a) => resolve(&base, a),
                };
                let method = if method == "post" {
                    Method::Post
                } else {
                    Method::Get
                };
                form = Some(FormAcc {
                    action,
                    method,
                    fields: BTreeSet::new(),
                    has_password: false,
                    offset: t.offset,
                });
            }
            ("form", true) => {
                if let Some(f) = form.take() {
                    sc.close_form(f);
                }
            }
            ("input" | "select" | "textarea" | "button", false) => {
                if let Some(f) = form.as_mut() {
                    if t.name == "input"
                        && t.attr("type")
                            .is_some_and(|ty| ty.trim().eq_ignore_ascii_case("password"))
                    {
                        f.has_password = true;
                    }
                    if let Some(name) = t
                        .attr("name")
                        .map(str::trim)
                        .filter(|n| !n.is_empty() && n.len() <= 128)
                    {
                        if f.fields.len() < crate::delta::MAX_FIELDS {
                            f.fields.insert(name.to_string());
                        }
                    }
                }
            }
            _ => {}
        }
        true
    });
    if let Some(f) = form.take() {
        sc.close_form(f);
    }
    sc.out.truncated |= truncated;
    Ok(sc.out)
}

#[cfg(test)]
mod tests {
    use super::*;

    const ORIGIN: &str = "https://docs.example.com";

    fn run(html: &str) -> ScanResult {
        scan(
            &ScanConfig::new(
                format!("{ORIGIN}/guide/install?lang=en"),
                vec![ORIGIN.into()],
            ),
            html,
        )
        .unwrap()
    }

    fn routes(r: &ScanResult) -> Vec<(String, &'static str)> {
        r.candidates
            .iter()
            .map(|c| (c.route.clone(), c.via.as_str()))
            .collect()
    }

    #[test]
    fn reports_only_what_the_markup_names() {
        let r = run(r#"<!doctype html><html><head>
            <link rel="stylesheet" href="/style.css"><link rel="Next" href="step-2">
            <script>location.href = "/secret"; var a = '<a href="/in-script">';</script>
            <style>a { background: url(/in-style) }</style>
            </head><body>
            <!-- <a href="/in-comment"> -->
            <a href="/guide/start?b=2&amp;a=1#top">Start</a>
            <a href='../account'>Account</a>
            <a href=/plain>plain</a>
            <a onclick="go('/onclick')" data-href="/data">no href</a>
            <img srcset="/img-1x.png 1x">
            <template><a href="/in-template">t</a></template>
            <textarea name="t"><a href="/in-textarea"></textarea>
            </body></html>"#);
        assert_eq!(
            routes(&r),
            vec![
                ("/guide/step-2".into(), "link"),
                ("/guide/start".into(), "a"),
                ("/account".into(), "a"),
                ("/plain".into(), "a"),
            ]
        );
        assert_eq!(r.candidates[1].query_keys, vec!["a", "b"]);
        assert!(!r.truncated);
    }

    #[test]
    fn counts_and_refuses_what_it_will_not_interpret() {
        let r = run(r##"
            <a href="https://other.example/x">ext</a>
            <a href="http://docs.example.com/insecure">http</a>
            <a href="javascript:alert(1)">js</a>
            <a href="https://u:p@docs.example.com/creds">creds</a>
            <a href="#same">frag</a><a href="">self</a>
            <a href="\\evil">bs</a>"##);
        assert!(r.candidates.is_empty());
        assert_eq!(r.external, 1);
        assert_eq!(r.rejected.get("scheme"), Some(&2));
        assert_eq!(r.rejected.get("credentials"), Some(&1));
        assert_eq!(r.rejected.get("malformed"), Some(&1));
    }

    #[test]
    fn forms_keep_field_names_never_values_and_skip_password_forms() {
        let r = run(r#"
            <form action="/account/delete" method="POST">
              <input type="hidden" name="csrf" value="SECRET-TOKEN">
              <input name="confirm" value="yes"><select name="reason"></select>
              <button name="go">Delete</button>
            </form>
            <form action="/login" method="post"><input name="user"><input type="password" name="pw"></form>
            <form><input name="q"></form>
            <form method="dialog"><button>close</button></form>"#);
        assert_eq!(r.candidates.len(), 2);
        let del = &r.candidates[0];
        assert_eq!(
            (del.route.as_str(), del.via, del.method),
            ("/account/delete", Via::Form, Method::Post)
        );
        assert_eq!(del.fields, vec!["confirm", "csrf", "go", "reason"]);
        let search = &r.candidates[1];
        assert_eq!(
            (search.route.as_str(), search.method),
            ("/guide/install", Method::Get)
        );
        assert_eq!(r.skipped_password_forms, 1);
        let text = crate::canon::to_string(&r.to_value());
        assert!(!text.contains("SECRET-TOKEN") && !text.contains("\"yes\""));
    }

    #[test]
    fn base_is_honoured_only_on_an_allowed_origin() {
        let r = run(r#"<base href="https://docs.example.com/v2/"><a href="intro">i</a>"#);
        assert_eq!(routes(&r), vec![("/v2/intro".into(), "a")]);
        let r = run(r#"<base href="https://evil.example/"><a href="intro">i</a>"#);
        assert_eq!(routes(&r), vec![("/guide/intro".into(), "a")]);
        assert_eq!(r.rejected.get("base"), Some(&1));
    }

    #[test]
    fn bounded_in_bytes_and_candidates() {
        let many: String = (0..50)
            .map(|i| format!("<a href=\"/p{i}\">x</a>"))
            .collect();
        let mut cfg = ScanConfig::new(format!("{ORIGIN}/"), vec![ORIGIN.into()]);
        cfg.max_candidates = 10;
        let r = scan(&cfg, &many).unwrap();
        assert_eq!(r.candidates.len(), 10);
        assert!(r.truncated);
        cfg.max_candidates = 512;
        cfg.max_bytes = 40;
        let r = scan(&cfg, &many).unwrap();
        assert!(r.truncated && r.candidates.len() <= 2);
    }

    #[test]
    fn zero_trust_configuration() {
        assert!(scan(&ScanConfig::new(format!("{ORIGIN}/"), vec![]), "").is_err());
        assert!(
            scan(
                &ScanConfig::new("https://other.example/", vec![ORIGIN.into()]),
                ""
            )
            .is_err()
        );
        assert!(
            scan(
                &ScanConfig::new(format!("{ORIGIN}/"), vec![format!("{ORIGIN}/")]),
                ""
            )
            .is_err()
        );
    }

    #[test]
    fn entities_decode_conservatively() {
        assert_eq!(
            decode_entities("/a?x=1&amp;y=2&#38;z&#x3D;3&bogus;&"),
            "/a?x=1&y=2&z=3&bogus;&"
        );
        assert_eq!(decode_entities("&#0;"), "\u{fffd}");
    }
}
