//! Canonical JSON with byte-for-byte parity with the Python reference.
//!
//! secdogie signs and content-addresses the bytes of
//! `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
//! (`secdogie_identity.signing.canonical`). Reproducing those bytes outside
//! Python takes more than "sorted keys, no spaces":
//!
//! * **Numbers keep Python's int/float split.** `json.loads` makes a float of
//!   any number token with a fraction or an exponent and an int of the rest, so
//!   `1.0` stays `1.0` and `1` stays `1`. Ints are kept as exact decimal digits
//!   (no 2**53 or 2**64 ceiling); `-0` as an int is `0`.
//! * **Floats print like `repr(float)`.** Shortest round-trip digits, fixed
//!   notation when the decimal point position `decpt` satisfies
//!   `-4 < decpt <= 16` (always with a fractional part, `.0` if none), otherwise
//!   `d[.ddd]e±XX` with at least two exponent digits. `-0.0` keeps its sign.
//! * **Keys sort by code point.** A `BTreeMap<String, _>` orders by UTF-8 bytes,
//!   which is code-point order -- the same as Python's `str` ordering.
//! * **Strings are raw UTF-8** except `"` `\\`, the short escapes
//!   `\b \f \n \r \t`, and other C0 controls as lower-case `\u00xx`.
//!
//! The parser is strict where Python is lenient, so a value either canonicalizes
//! identically in every language or is refused: `NaN` / `Infinity` / overflow
//! to infinity, lone surrogates and duplicate keys are errors here (Python
//! would emit `NaN`, raise late, or keep the last key respectively). See
//! `fixtures/vectors/canonical.json`.

use std::collections::BTreeMap;
use std::fmt::Write as _;

/// A parsed JSON value that remembers what Python would have parsed.
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    /// An integer as normalized decimal digits (`-?(0|[1-9][0-9]*)`, never `-0`).
    Int(String),
    /// A finite float.
    Float(f64),
    Str(String),
    Arr(Vec<Value>),
    Obj(BTreeMap<String, Value>),
}

impl Value {
    pub fn int(n: i64) -> Value {
        Value::Int(n.to_string())
    }

    pub fn str(s: impl Into<String>) -> Value {
        Value::Str(s.into())
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_obj(&self) -> Option<&BTreeMap<String, Value>> {
        match self {
            Value::Obj(m) => Some(m),
            _ => None,
        }
    }

    pub fn as_arr(&self) -> Option<&[Value]> {
        match self {
            Value::Arr(a) => Some(a),
            _ => None,
        }
    }

    /// The value as a `u64`, if it is an integer (not a float) in range.
    pub fn as_u64(&self) -> Option<u64> {
        match self {
            Value::Int(d) => d.parse().ok(),
            _ => None,
        }
    }
}

/// Builds an object value from `(key, value)` pairs.
pub fn obj<I, K>(pairs: I) -> Value
where
    I: IntoIterator<Item = (K, Value)>,
    K: Into<String>,
{
    Value::Obj(pairs.into_iter().map(|(k, v)| (k.into(), v)).collect())
}

/// Builds an array of strings.
pub fn str_arr<I, S>(items: I) -> Value
where
    I: IntoIterator<Item = S>,
    S: Into<String>,
{
    Value::Arr(items.into_iter().map(|s| Value::Str(s.into())).collect())
}

#[derive(Clone, Copy, Debug)]
pub struct Limits {
    pub max_bytes: usize,
    pub max_depth: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Limits {
            max_bytes: 1 << 20,
            max_depth: 64,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ParseError {
    pub reason: &'static str,
    pub offset: usize,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} at byte {}", self.reason, self.offset)
    }
}

impl std::error::Error for ParseError {}

/// Parses `text` strictly, with default limits.
pub fn parse(text: &str) -> Result<Value, ParseError> {
    parse_with(text, Limits::default())
}

pub fn parse_with(text: &str, limits: Limits) -> Result<Value, ParseError> {
    if text.len() > limits.max_bytes {
        return Err(ParseError {
            reason: "input too large",
            offset: limits.max_bytes,
        });
    }
    let mut p = Parser {
        s: text.as_bytes(),
        i: 0,
        depth: 0,
        max_depth: limits.max_depth,
    };
    p.ws();
    let v = p.value()?;
    p.ws();
    if p.i != p.s.len() {
        return Err(p.err("trailing data"));
    }
    Ok(v)
}

struct Parser<'a> {
    s: &'a [u8],
    i: usize,
    depth: usize,
    max_depth: usize,
}

impl Parser<'_> {
    fn err(&self, reason: &'static str) -> ParseError {
        ParseError {
            reason,
            offset: self.i,
        }
    }

    fn peek(&self) -> Option<u8> {
        self.s.get(self.i).copied()
    }

    fn ws(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.i += 1;
        }
    }

    fn eat(&mut self, lit: &[u8]) -> bool {
        if self.s[self.i..].starts_with(lit) {
            self.i += lit.len();
            true
        } else {
            false
        }
    }

    fn value(&mut self) -> Result<Value, ParseError> {
        match self.peek() {
            None => Err(self.err("invalid JSON")),
            Some(b'{') => self.object(),
            Some(b'[') => self.array(),
            Some(b'"') => Ok(Value::Str(self.string()?)),
            Some(b't') if self.eat(b"true") => Ok(Value::Bool(true)),
            Some(b'f') if self.eat(b"false") => Ok(Value::Bool(false)),
            Some(b'n') if self.eat(b"null") => Ok(Value::Null),
            // Python's json.loads accepts these; nothing here does.
            Some(b'N') if self.s[self.i..].starts_with(b"NaN") => {
                Err(self.err("non-finite number"))
            }
            Some(b'I') if self.s[self.i..].starts_with(b"Infinity") => {
                Err(self.err("non-finite number"))
            }
            Some(b'-') if self.s[self.i..].starts_with(b"-Infinity") => {
                Err(self.err("non-finite number"))
            }
            Some(b'-' | b'0'..=b'9') => self.number(),
            Some(_) => Err(self.err("invalid JSON")),
        }
    }

    fn enter(&mut self) -> Result<(), ParseError> {
        self.depth += 1;
        if self.depth > self.max_depth {
            return Err(self.err("nesting too deep"));
        }
        Ok(())
    }

    fn object(&mut self) -> Result<Value, ParseError> {
        self.enter()?;
        self.i += 1;
        let mut m = BTreeMap::new();
        self.ws();
        if self.peek() == Some(b'}') {
            self.i += 1;
            self.depth -= 1;
            return Ok(Value::Obj(m));
        }
        loop {
            self.ws();
            if self.peek() != Some(b'"') {
                return Err(self.err("invalid JSON"));
            }
            let at = self.i;
            let k = self.string()?;
            self.ws();
            if self.peek() != Some(b':') {
                return Err(self.err("invalid JSON"));
            }
            self.i += 1;
            self.ws();
            let v = self.value()?;
            if m.insert(k, v).is_some() {
                return Err(ParseError {
                    reason: "duplicate key",
                    offset: at,
                });
            }
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b'}') => {
                    self.i += 1;
                    self.depth -= 1;
                    return Ok(Value::Obj(m));
                }
                _ => return Err(self.err("invalid JSON")),
            }
        }
    }

    fn array(&mut self) -> Result<Value, ParseError> {
        self.enter()?;
        self.i += 1;
        let mut a = Vec::new();
        self.ws();
        if self.peek() == Some(b']') {
            self.i += 1;
            self.depth -= 1;
            return Ok(Value::Arr(a));
        }
        loop {
            self.ws();
            a.push(self.value()?);
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b']') => {
                    self.i += 1;
                    self.depth -= 1;
                    return Ok(Value::Arr(a));
                }
                _ => return Err(self.err("invalid JSON")),
            }
        }
    }

    fn hex4(&mut self) -> Result<u32, ParseError> {
        let Some(h) = self.s.get(self.i..self.i + 4) else {
            return Err(self.err("invalid escape"));
        };
        let mut v = 0u32;
        for &c in h {
            let d = (c as char)
                .to_digit(16)
                .ok_or_else(|| self.err("invalid escape"))?;
            v = v * 16 + d;
        }
        self.i += 4;
        Ok(v)
    }

    fn string(&mut self) -> Result<String, ParseError> {
        self.i += 1; // opening quote
        let mut out = String::new();
        loop {
            let start = self.i;
            while let Some(c) = self.peek() {
                if c == b'"' || c == b'\\' || c < 0x20 {
                    break;
                }
                self.i += 1;
            }
            // The input is a &str and we only stopped at ASCII bytes, so this
            // slice is valid UTF-8.
            out.push_str(
                std::str::from_utf8(&self.s[start..self.i])
                    .map_err(|_| self.err("invalid UTF-8"))?,
            );
            match self.peek() {
                None => return Err(self.err("unterminated string")),
                Some(b'"') => {
                    self.i += 1;
                    return Ok(out);
                }
                Some(b'\\') => {
                    self.i += 1;
                    let Some(e) = self.peek() else {
                        return Err(self.err("invalid escape"));
                    };
                    self.i += 1;
                    match e {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let hi = self.hex4()?;
                            let cp = if (0xD800..0xDC00).contains(&hi) {
                                if !self.eat(b"\\u") {
                                    return Err(self.err("lone surrogate"));
                                }
                                let lo = self.hex4()?;
                                if !(0xDC00..0xE000).contains(&lo) {
                                    return Err(self.err("lone surrogate"));
                                }
                                0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00)
                            } else if (0xDC00..0xE000).contains(&hi) {
                                return Err(self.err("lone surrogate"));
                            } else {
                                hi
                            };
                            out.push(char::from_u32(cp).ok_or_else(|| self.err("invalid escape"))?);
                        }
                        _ => return Err(self.err("invalid escape")),
                    }
                }
                Some(_) => return Err(self.err("control character in string")),
            }
        }
    }

    fn digits(&mut self) -> usize {
        let start = self.i;
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.i += 1;
        }
        self.i - start
    }

    fn number(&mut self) -> Result<Value, ParseError> {
        let start = self.i;
        let neg = self.peek() == Some(b'-');
        if neg {
            self.i += 1;
        }
        let int_start = self.i;
        let n = self.digits();
        if n == 0 || (n > 1 && self.s[int_start] == b'0') {
            return Err(ParseError {
                reason: "invalid number",
                offset: start,
            });
        }
        let mut is_float = false;
        if self.peek() == Some(b'.') {
            self.i += 1;
            if self.digits() == 0 {
                return Err(ParseError {
                    reason: "invalid number",
                    offset: start,
                });
            }
            is_float = true;
        }
        if matches!(self.peek(), Some(b'e' | b'E')) {
            self.i += 1;
            if matches!(self.peek(), Some(b'+' | b'-')) {
                self.i += 1;
            }
            if self.digits() == 0 {
                return Err(ParseError {
                    reason: "invalid number",
                    offset: start,
                });
            }
            is_float = true;
        }
        // Only ASCII digits, signs, '.', 'e' were consumed.
        let tok =
            std::str::from_utf8(&self.s[start..self.i]).map_err(|_| self.err("invalid number"))?;
        if is_float {
            let f: f64 = tok.parse().map_err(|_| ParseError {
                reason: "invalid number",
                offset: start,
            })?;
            if !f.is_finite() {
                return Err(ParseError {
                    reason: "non-finite number",
                    offset: start,
                });
            }
            return Ok(Value::Float(f));
        }
        let digits = &tok[usize::from(neg)..];
        if digits == "0" {
            return Ok(Value::Int("0".into()));
        }
        Ok(Value::Int(tok.to_string()))
    }
}

/// Canonical bytes of `v` (see the module docs).
pub fn to_bytes(v: &Value) -> Vec<u8> {
    to_string(v).into_bytes()
}

pub fn to_string(v: &Value) -> String {
    let mut out = String::new();
    write_value(v, &mut out);
    out
}

fn write_value(v: &Value, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(d) => out.push_str(d),
        Value::Float(f) => out.push_str(&py_float_repr(*f)),
        Value::Str(s) => write_str(s, out),
        Value::Arr(a) => {
            out.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_value(x, out);
            }
            out.push(']');
        }
        Value::Obj(m) => {
            out.push('{');
            for (i, (k, x)) in m.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_str(k, out);
                out.push(':');
                write_value(x, out);
            }
            out.push('}');
        }
    }
}

fn write_str(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `repr(x)` for a finite float, exactly as CPython prints it.
pub fn py_float_repr(x: f64) -> String {
    if x == 0.0 {
        return if x.is_sign_negative() {
            "-0.0".into()
        } else {
            "0.0".into()
        };
    }
    // Rust's `{:e}` gives the shortest digits that round-trip, closest to x --
    // the same digits as CPython's repr ('r' mode).
    let sci = format!("{:e}", x.abs());
    let (mant, exp) = sci.split_once('e').unwrap_or((sci.as_str(), "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let digits: String = mant.chars().filter(char::is_ascii_digit).collect();
    let decpt = exp + 1; // value = 0.DIGITS x 10^decpt
    let mut out = String::new();
    if x < 0.0 {
        out.push('-');
    }
    let nd = digits.len() as i32;
    if decpt <= -4 || decpt > 16 {
        out.push_str(&digits[..1]);
        if nd > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let _ = write!(out, "e{}{:02}", if exp < 0 { '-' } else { '+' }, exp.abs());
    } else if decpt <= 0 {
        out.push_str("0.");
        out.push_str(&"0".repeat((-decpt) as usize));
        out.push_str(&digits);
    } else if decpt >= nd {
        out.push_str(&digits);
        out.push_str(&"0".repeat((decpt - nd) as usize));
        out.push_str(".0");
    } else {
        out.push_str(&digits[..decpt as usize]);
        out.push('.');
        out.push_str(&digits[decpt as usize..]);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn canon(s: &str) -> String {
        to_string(&parse(s).unwrap())
    }

    #[test]
    fn floats_print_like_python_repr() {
        let cases = [
            (1.0, "1.0"),
            (-0.0, "-0.0"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (9999999999999998.0, "9999999999999998.0"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (0.1 + 0.2, "0.30000000000000004"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
            (1759740120.123456, "1759740120.123456"),
            (-2.5, "-2.5"),
            (123e-20, "1.23e-18"),
        ];
        for (x, want) in cases {
            assert_eq!(py_float_repr(x), want, "{x:?}");
        }
    }

    #[test]
    fn int_and_float_stay_distinct() {
        assert_eq!(canon("[1,1.0,-0,-0.0,1E2]"), "[1,1.0,0,-0.0,100.0]");
        assert_eq!(
            canon("123456789012345678901234567890"),
            "123456789012345678901234567890"
        );
    }

    #[test]
    fn keys_sort_by_code_point() {
        assert_eq!(
            canon(r#"{"\uffff":1,"\ud83d\ude00":2,"a":3}"#),
            "{\"a\":3,\"\u{ffff}\":1,\"\u{1F600}\":2}"
        );
    }

    #[test]
    fn strict_where_python_is_lenient() {
        for (text, reason) in [
            ("NaN", "non-finite number"),
            ("[-Infinity]", "non-finite number"),
            ("1e400", "non-finite number"),
            ("\"\\ud800\"", "lone surrogate"),
            ("\"\\udc00\"", "lone surrogate"),
            ("{\"a\":1,\"a\":2}", "duplicate key"),
            ("[01]", "invalid number"),
            ("1.", "invalid number"),
            ("{} {}", "trailing data"),
            ("\"a\nb\"", "control character in string"),
        ] {
            assert_eq!(parse(text).unwrap_err().reason, reason, "{text}");
        }
    }

    #[test]
    fn depth_and_size_are_bounded() {
        let deep = "[".repeat(100) + &"]".repeat(100);
        assert_eq!(parse(&deep).unwrap_err().reason, "nesting too deep");
        let lim = Limits {
            max_bytes: 4,
            max_depth: 8,
        };
        assert_eq!(
            parse_with("[1,2,3]", lim).unwrap_err().reason,
            "input too large"
        );
    }
}
