//! The conservative subset of URL handling the topology layer needs: absolute
//! `https:` URLs and RFC 3986 reference resolution against one.
//!
//! "Conservative" means refusing rather than guessing. There is no IDNA, no
//! IPv6 literal, no userinfo, no scheme but `https`, no backslash-as-slash
//! leniency. A URL that would need any of those is reported as rejected, never
//! repaired into something else. Fragments are dropped (same document), and a
//! query is reduced to its sorted key *names* -- values can carry tokens and
//! are never kept.

const MAX_ROUTE: usize = 2048;
const MAX_QUERY_KEYS: usize = 64;
const MAX_KEY_LEN: usize = 128;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HttpsUrl {
    /// Lower-case ASCII host name.
    pub host: String,
    /// `None` for the default port 443.
    pub port: Option<u16>,
    /// Normalized path: starts with `/`, no dot segments, percent-encoded.
    pub path: String,
    /// The raw query (without `?`), if any.
    pub query: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Reject {
    /// Not `https:` (`http:`, `javascript:`, `data:`, `mailto:`, ...).
    Scheme,
    /// Userinfo in the authority (`https://user:pass@host`).
    Credentials,
    /// A host this layer will not interpret (IDN, IPv6 literal, odd characters).
    Host,
    Malformed,
}

impl Reject {
    pub fn label(self) -> &'static str {
        match self {
            Reject::Scheme => "scheme",
            Reject::Credentials => "credentials",
            Reject::Host => "host",
            Reject::Malformed => "malformed",
        }
    }
}

impl HttpsUrl {
    pub fn origin(&self) -> String {
        match self.port {
            Some(p) => format!("https://{}:{p}", self.host),
            None => format!("https://{}", self.host),
        }
    }

    /// The query's key names, sorted and de-duplicated. Values are dropped.
    pub fn query_keys(&self) -> Vec<String> {
        let mut keys: Vec<String> = self
            .query
            .as_deref()
            .unwrap_or("")
            .split('&')
            .filter_map(|part| part.split('=').next())
            .filter(|k| !k.is_empty() && k.len() <= MAX_KEY_LEN)
            .map(percent_encode)
            .collect();
        keys.sort();
        keys.dedup();
        keys.truncate(MAX_QUERY_KEYS);
        keys
    }
}

fn has_scheme(s: &str) -> Option<&str> {
    let end = s.find(':')?;
    let scheme = &s[..end];
    let mut chars = scheme.chars();
    let first = chars.next()?;
    if first.is_ascii_alphabetic()
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'))
    {
        Some(scheme)
    } else {
        None
    }
}

/// Parses an absolute `https:` URL.
pub fn parse_https(s: &str) -> Result<HttpsUrl, Reject> {
    let Some(scheme) = has_scheme(s) else {
        return Err(Reject::Malformed);
    };
    if !scheme.eq_ignore_ascii_case("https") {
        return Err(Reject::Scheme);
    }
    let Some(rest) = s[scheme.len() + 1..].strip_prefix("//") else {
        return Err(Reject::Malformed);
    };
    if s.contains('\\') {
        return Err(Reject::Malformed);
    }
    let auth_end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let (authority, tail) = rest.split_at(auth_end);
    if authority.contains('@') {
        return Err(Reject::Credentials);
    }
    let (host, port) = parse_authority(authority)?;
    let (path, query) = split_path_query(tail);
    let path = if path.is_empty() {
        "/".to_string()
    } else {
        normalize_path(path)
    };
    Ok(HttpsUrl {
        host,
        port,
        path,
        query: query.map(str::to_string),
    })
}

fn parse_authority(authority: &str) -> Result<(String, Option<u16>), Reject> {
    if authority.starts_with('[') {
        return Err(Reject::Host);
    }
    let (host, port) = match authority.rsplit_once(':') {
        Some((h, p)) => (h, Some(p)),
        None => (authority, None),
    };
    if host.is_empty() {
        return Err(Reject::Malformed);
    }
    if !host.is_ascii() {
        return Err(Reject::Host);
    }
    let host = host.to_ascii_lowercase();
    let ok_chars = host
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b'_'));
    if !ok_chars || host.split('.').any(str::is_empty) {
        return Err(Reject::Host);
    }
    let port = match port {
        None | Some("") => None,
        Some(p) => {
            if p.len() > 5 || !p.bytes().all(|b| b.is_ascii_digit()) {
                return Err(Reject::Malformed);
            }
            match p.parse::<u32>() {
                Ok(443) => None,
                Ok(n @ 1..=65535) => Some(n as u16),
                _ => return Err(Reject::Malformed),
            }
        }
    };
    Ok((host, port))
}

/// Splits `path?query#fragment`, dropping the fragment.
fn split_path_query(s: &str) -> (&str, Option<&str>) {
    let s = s.split('#').next().unwrap_or("");
    match s.split_once('?') {
        Some((p, q)) => (p, Some(q)),
        None => (s, None),
    }
}

/// Percent-encodes what WHATWG's path percent-encode set covers (C0 controls,
/// space, `"`, `#`, `<`, `>`, `?`, backtick, braces) plus every non-ASCII byte.
/// Existing `%XX` sequences are left alone, so this is idempotent.
fn percent_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        if (0x21..0x7f).contains(&b)
            && !matches!(b, b'"' | b'<' | b'>' | b'`' | b'{' | b'}' | b'#' | b'?')
        {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn is_dot(seg: &str) -> bool {
    seg == "." || seg.eq_ignore_ascii_case("%2e")
}

fn is_dotdot(seg: &str) -> bool {
    ["..", ".%2e", "%2e.", "%2e%2e"]
        .iter()
        .any(|d| seg.eq_ignore_ascii_case(d))
}

/// RFC 3986 section 5.2.4 over an absolute path (one starting with `/`).
fn remove_dot_segments(path: &str) -> String {
    let segs: Vec<&str> = path.trim_start_matches('/').split('/').collect();
    let mut out: Vec<&str> = Vec::with_capacity(segs.len());
    let n = segs.len();
    for (i, seg) in segs.iter().enumerate() {
        let last = i + 1 == n;
        if is_dot(seg) {
            if last {
                out.push("");
            }
        } else if is_dotdot(seg) {
            out.pop();
            if last {
                out.push("");
            }
        } else {
            out.push(seg);
        }
    }
    format!("/{}", out.join("/"))
}

fn normalize_path(path: &str) -> String {
    let p = if path.starts_with('/') {
        path.to_string()
    } else {
        format!("/{path}")
    };
    remove_dot_segments(&percent_encode(&p))
}

/// Resolves an `href` / `action` against `base`. `Ok(None)` means "this same
/// document" (empty or fragment-only reference).
pub fn resolve(base: &HttpsUrl, reference: &str) -> Result<Option<HttpsUrl>, Reject> {
    // HTML strips leading/trailing ASCII whitespace and drops tab/newline
    // inside URL attributes; anything subtler is refused below.
    let r: String = reference
        .trim_matches(|c: char| matches!(c, ' ' | '\t' | '\n' | '\r' | '\u{c}'))
        .chars()
        .filter(|c| !matches!(c, '\t' | '\n' | '\r'))
        .collect();
    if r.contains('\\') {
        return Err(Reject::Malformed);
    }
    if r.is_empty() || r.starts_with('#') {
        return Ok(None);
    }
    if let Some(scheme) = has_scheme(&r) {
        if !scheme.eq_ignore_ascii_case("https") {
            return Err(Reject::Scheme);
        }
        return parse_https(&r).map(Some);
    }
    if r.starts_with("//") {
        return parse_https(&format!("https:{r}")).map(Some);
    }
    let (path_part, query) = split_path_query(&r);
    let (path, query) = if path_part.starts_with('/') {
        (normalize_path(path_part), query.map(str::to_string))
    } else if path_part.is_empty() {
        (
            base.path.clone(),
            query.map(str::to_string).or_else(|| base.query.clone()),
        )
    } else {
        let dir = &base.path[..=base.path.rfind('/').unwrap_or(0)];
        (
            normalize_path(&format!("{dir}{path_part}")),
            query.map(str::to_string),
        )
    };
    Ok(Some(HttpsUrl {
        host: base.host.clone(),
        port: base.port,
        path,
        query,
    }))
}

/// Whether `s` is exactly a canonical https origin (`https://host[:port]`).
pub fn is_origin(s: &str) -> bool {
    match parse_https(s) {
        Ok(u) => u.path == "/" && u.query.is_none() && !s.ends_with('/') && u.origin() == s,
        Err(_) => false,
    }
}

/// Whether `s` is a normalized route as `resolve` produces it.
pub fn is_route(s: &str) -> bool {
    s.starts_with('/') && s.len() <= MAX_ROUTE && normalize_path(s) == s
}

/// Whether `k` is a query key name as `query_keys` produces it.
pub fn is_query_key(k: &str) -> bool {
    !k.is_empty() && k.len() <= MAX_KEY_LEN * 3 && percent_encode(k) == k && !k.contains(['&', '='])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> HttpsUrl {
        parse_https("https://Docs.Example.com:443/guide/install/index.html?lang=en#top").unwrap()
    }

    #[test]
    fn parses_and_normalizes() {
        let b = base();
        assert_eq!(b.origin(), "https://docs.example.com");
        assert_eq!(b.path, "/guide/install/index.html");
        assert_eq!(b.query_keys(), vec!["lang"]);
        assert_eq!(
            parse_https("https://a.example:8443").unwrap().origin(),
            "https://a.example:8443"
        );
    }

    #[test]
    fn refuses_rather_than_guesses() {
        assert_eq!(parse_https("http://a.example/"), Err(Reject::Scheme));
        assert_eq!(
            parse_https("https://user:pw@a.example/"),
            Err(Reject::Credentials)
        );
        assert_eq!(parse_https("https://[::1]/"), Err(Reject::Host));
        assert_eq!(
            parse_https("https://b\u{fc}cher.example/"),
            Err(Reject::Host)
        );
        assert_eq!(parse_https("https://a..example/"), Err(Reject::Host));
        assert_eq!(
            parse_https("https://a.example:99999/"),
            Err(Reject::Malformed)
        );
        assert_eq!(parse_https("https:a.example"), Err(Reject::Malformed));
        let b = base();
        assert_eq!(resolve(&b, "javascript:alert(1)"), Err(Reject::Scheme));
        assert_eq!(resolve(&b, "mailto:a@b.c"), Err(Reject::Scheme));
        assert_eq!(resolve(&b, "\\\\evil.example\\x"), Err(Reject::Malformed));
    }

    #[test]
    fn resolves_references_per_rfc3986() {
        let b = base();
        let r = |s: &str| {
            resolve(&b, s)
                .unwrap()
                .map(|u| (u.origin(), u.path.clone(), u.query_keys()))
        };
        let o = "https://docs.example.com".to_string();
        assert_eq!(
            r("../start?x=1&y=2&x=3"),
            Some((
                o.clone(),
                "/guide/start".into(),
                vec!["x".into(), "y".into()]
            ))
        );
        assert_eq!(r("/a/./b/../c/"), Some((o.clone(), "/a/c/".into(), vec![])));
        assert_eq!(
            r("next"),
            Some((o.clone(), "/guide/install/next".into(), vec![]))
        );
        assert_eq!(
            r("?q=1"),
            Some((
                o.clone(),
                "/guide/install/index.html".into(),
                vec!["q".into()]
            ))
        );
        assert_eq!(
            r("//other.example/p"),
            Some(("https://other.example".into(), "/p".into(), vec![]))
        );
        assert_eq!(r("/%2e%2E/x"), Some((o.clone(), "/x".into(), vec![])));
        assert_eq!(r("/a b/\u{e9}"), Some((o, "/a%20b/%C3%A9".into(), vec![])));
        assert_eq!(r("#frag"), None);
        assert_eq!(r("  "), None);
    }

    #[test]
    fn origin_and_route_predicates() {
        assert!(is_origin("https://docs.example.com"));
        assert!(is_origin("https://docs.example.com:8443"));
        assert!(!is_origin("https://docs.example.com/"));
        assert!(!is_origin("https://Docs.example.com"));
        assert!(!is_origin("https://docs.example.com:443"));
        assert!(!is_origin("http://docs.example.com"));
        assert!(is_route("/guide/install"));
        assert!(!is_route("/a/../b"));
        assert!(!is_route("/a b"));
        assert!(!is_route("relative"));
        assert!(is_query_key("lang"));
        assert!(!is_query_key("a=b"));
    }
}
