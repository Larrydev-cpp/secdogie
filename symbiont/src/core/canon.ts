/**
 * Canonical JSON with byte-for-byte parity with the Python reference
 * (`secdogie_identity.signing.canonical`):
 *
 *   json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
 *
 * JavaScript's own JSON gets four things wrong for this purpose, all verified
 * against fixtures/vectors/canonical.json:
 *
 *  1. **int vs float.** Python keeps `1.0` a float and prints `1.0`;
 *     `JSON.parse` + `JSON.stringify` turn it into `1`. A float must therefore
 *     be wrapped: {@link pyFloat}. A bare `number` here means an integer and is
 *     refused if it is not a safe one; `bigint` carries integers past 2**53
 *     (the dialogue header's `timestamp_ns` is ~1.76e18).
 *  2. **Float text.** Python prints `repr(x)`: shortest round-trip digits,
 *     fixed notation while -4 < decpt <= 16, else `d.ddde±XX`
 *     (`1e+16`, `1e-05`), and `-0.0` keeps its sign.
 *  3. **Key order.** Python sorts by code point; `Array.prototype.sort` sorts
 *     by UTF-16 unit, which puts U+1F600 before U+FFFF.
 *  4. **Leniency.** Python emits `NaN`, keeps the last duplicate key, and only
 *     fails on a lone surrogate when encoding. Here all three are refused, so a
 *     value either canonicalizes identically in every language or not at all.
 *
 * {@link parseLossless} is the matching reader: it tags number tokens exactly
 * as `json.loads` does (fraction or exponent -> float), so a Python-signed
 * object re-encodes to the very bytes that were signed.
 */

/** A float, printed the way Python's `repr(float)` prints it. */
export class PyFloat {
  readonly value: number;
  constructor(value: number) {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      throw new CanonError('non-finite number');
    }
    this.value = value;
  }
}

export const pyFloat = (value: number): PyFloat => new PyFloat(value);

export type CanonValue =
  | null
  | boolean
  | string
  /** An integer. Must be a safe integer; use bigint beyond 2**53. */
  | number
  | bigint
  | PyFloat
  | readonly CanonValue[]
  | CanonObject;

export interface CanonObject {
  readonly [key: string]: CanonValue;
}

export class CanonError extends Error {
  readonly reason: string;
  readonly offset: number;
  constructor(reason: string, offset = -1) {
    super(offset >= 0 ? `${reason} at offset ${offset}` : reason);
    this.reason = reason;
    this.offset = offset;
  }
}

export interface ParseLimits {
  maxLength: number;
  maxDepth: number;
}

const DEFAULT_LIMITS: ParseLimits = { maxLength: 1 << 20, maxDepth: 64 };

/** Integers outside this range become bigint so nothing is rounded. */
const isSafe = (n: number): boolean => Number.isSafeInteger(n);

function isPlainObject(v: unknown): v is CanonObject {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return false;
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

/** Code-point order (Python's `str` order), not UTF-16 unit order. */
export function compareCodePoints(a: string, b: string): number {
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const x = a.codePointAt(i)!;
    const y = b.codePointAt(j)!;
    if (x !== y) return x < y ? -1 : 1;
    i += x > 0xffff ? 2 : 1;
    j += y > 0xffff ? 2 : 1;
  }
  return a.length - i === 0 ? (b.length - j === 0 ? 0 : -1) : 1;
}

/** `repr(x)` for a finite float, exactly as CPython prints it. */
export function pyFloatRepr(x: number): string {
  if (!Number.isFinite(x)) throw new CanonError('non-finite number');
  if (x === 0) return Object.is(x, -0) ? '-0.0' : '0.0';
  // String(x) is the shortest round-trip, closest decimal (ECMA-262
  // Number::toString) -- the same digits as CPython's repr.
  const s = String(Math.abs(x));
  const e = s.indexOf('e');
  const mant = e >= 0 ? s.slice(0, e) : s;
  const exp10 = e >= 0 ? Number(s.slice(e + 1)) : 0;
  const dot = mant.indexOf('.');
  const intPart = dot >= 0 ? mant.slice(0, dot) : mant;
  const frac = dot >= 0 ? mant.slice(dot + 1) : '';
  let digits = intPart + frac;
  let decpt = intPart.length + exp10; // value = 0.DIGITS x 10^decpt
  const lead = digits.length - digits.replace(/^0+/, '').length;
  digits = digits.slice(lead);
  decpt -= lead;
  digits = digits.replace(/0+$/, '');
  const sign = x < 0 ? '-' : '';
  const nd = digits.length;
  if (decpt <= -4 || decpt > 16) {
    const exp = decpt - 1;
    const m = nd > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
    const ex = String(Math.abs(exp)).padStart(2, '0');
    return `${sign}${m}e${exp < 0 ? '-' : '+'}${ex}`;
  }
  if (decpt <= 0) return `${sign}0.${'0'.repeat(-decpt)}${digits}`;
  if (decpt >= nd) return `${sign}${digits}${'0'.repeat(decpt - nd)}.0`;
  return `${sign}${digits.slice(0, decpt)}.${digits.slice(decpt)}`;
}

function writeString(s: string, out: string[]): void {
  if (!s.isWellFormed()) throw new CanonError('lone surrogate');
  let buf = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 0x22) buf += '\\"';
    else if (c === 0x5c) buf += '\\\\';
    else if (c === 0x08) buf += '\\b';
    else if (c === 0x0c) buf += '\\f';
    else if (c === 0x0a) buf += '\\n';
    else if (c === 0x0d) buf += '\\r';
    else if (c === 0x09) buf += '\\t';
    else if (c < 0x20) buf += `\\u${c.toString(16).padStart(4, '0')}`;
    else buf += s[i];
  }
  out.push(buf + '"');
}

function writeValue(v: CanonValue, out: string[], depth: number): void {
  if (depth > DEFAULT_LIMITS.maxDepth) throw new CanonError('nesting too deep');
  if (v === null) out.push('null');
  else if (v === true) out.push('true');
  else if (v === false) out.push('false');
  else if (typeof v === 'string') writeString(v, out);
  else if (typeof v === 'number') {
    if (!isSafe(v)) {
      throw new CanonError(
        Number.isFinite(v) && Number.isInteger(v)
          ? 'integer beyond 2**53: pass a bigint'
          : 'a bare number is an integer here: wrap floats with pyFloat()',
      );
    }
    out.push(Object.is(v, -0) ? '0' : String(v));
  } else if (typeof v === 'bigint') out.push(v.toString());
  else if (v instanceof PyFloat) out.push(pyFloatRepr(v.value));
  else if (Array.isArray(v)) {
    out.push('[');
    (v as readonly CanonValue[]).forEach((x, i) => {
      if (i > 0) out.push(',');
      writeValue(x, out, depth + 1);
    });
    out.push(']');
  } else if (isPlainObject(v)) {
    const keys = Object.keys(v).sort(compareCodePoints);
    out.push('{');
    keys.forEach((k, i) => {
      if (i > 0) out.push(',');
      writeString(k, out);
      out.push(':');
      const x = v[k];
      if (x === undefined) throw new CanonError(`undefined value at key ${JSON.stringify(k)}`);
      writeValue(x, out, depth + 1);
    });
    out.push('}');
  } else {
    throw new CanonError(`not a canonical JSON value: ${typeof v}`);
  }
}

/** Canonical JSON text. Also the wire form: Python re-reads it to the same bytes. */
export function canonicalText(v: CanonValue): string {
  const out: string[] = [];
  writeValue(v, out, 0);
  return out.join('');
}

export function canonicalBytes(v: CanonValue): Uint8Array<ArrayBuffer> {
  return new TextEncoder().encode(canonicalText(v));
}

/**
 * Parses JSON text the way Python's `json.loads` would (number tokens with a
 * fraction or exponent become {@link PyFloat}, the rest integers -- `number`
 * when safe, `bigint` otherwise), but strictly: NaN/Infinity, lone surrogates,
 * duplicate keys, raw control characters and trailing data are refused.
 * Objects come back with a null prototype, so a `__proto__` key is just a key.
 */
export function parseLossless(text: string, limits: Partial<ParseLimits> = {}): CanonValue {
  const lim = { ...DEFAULT_LIMITS, ...limits };
  if (text.length > lim.maxLength) throw new CanonError('input too large', lim.maxLength);
  if (!text.isWellFormed()) throw new CanonError('lone surrogate');
  const p = new Parser(text, lim.maxDepth);
  p.ws();
  const v = p.value();
  p.ws();
  if (p.i !== text.length) throw new CanonError('trailing data', p.i);
  return v;
}

class Parser {
  readonly s: string;
  readonly maxDepth: number;
  i = 0;
  depth = 0;

  constructor(s: string, maxDepth: number) {
    this.s = s;
    this.maxDepth = maxDepth;
  }

  err(reason: string, at = this.i): CanonError {
    return new CanonError(reason, at);
  }

  ws(): void {
    for (;;) {
      const c = this.s.charCodeAt(this.i);
      if (c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) this.i++;
      else return;
    }
  }

  value(): CanonValue {
    const s = this.s;
    const c = s[this.i];
    if (c === undefined) throw this.err('invalid JSON');
    if (c === '{') return this.object();
    if (c === '[') return this.array();
    if (c === '"') return this.string();
    if (s.startsWith('true', this.i)) return (this.i += 4), true;
    if (s.startsWith('false', this.i)) return (this.i += 5), false;
    if (s.startsWith('null', this.i)) return (this.i += 4), null;
    if (s.startsWith('NaN', this.i) || s.startsWith('Infinity', this.i) || s.startsWith('-Infinity', this.i)) {
      throw this.err('non-finite number');
    }
    if (c === '-' || (c >= '0' && c <= '9')) return this.number();
    throw this.err('invalid JSON');
  }

  enter(): void {
    if (++this.depth > this.maxDepth) throw this.err('nesting too deep');
  }

  object(): CanonObject {
    this.enter();
    this.i++;
    const o: Record<string, CanonValue> = Object.create(null);
    this.ws();
    if (this.s[this.i] === '}') {
      this.i++;
      this.depth--;
      return o;
    }
    for (;;) {
      this.ws();
      if (this.s[this.i] !== '"') throw this.err('invalid JSON');
      const at = this.i;
      const k = this.string();
      this.ws();
      if (this.s[this.i] !== ':') throw this.err('invalid JSON');
      this.i++;
      this.ws();
      const v = this.value();
      if (Object.hasOwn(o, k)) throw this.err('duplicate key', at);
      o[k] = v;
      this.ws();
      const d = this.s[this.i];
      if (d === ',') this.i++;
      else if (d === '}') {
        this.i++;
        this.depth--;
        return o;
      } else throw this.err('invalid JSON');
    }
  }

  array(): CanonValue[] {
    this.enter();
    this.i++;
    const a: CanonValue[] = [];
    this.ws();
    if (this.s[this.i] === ']') {
      this.i++;
      this.depth--;
      return a;
    }
    for (;;) {
      this.ws();
      a.push(this.value());
      this.ws();
      const d = this.s[this.i];
      if (d === ',') this.i++;
      else if (d === ']') {
        this.i++;
        this.depth--;
        return a;
      } else throw this.err('invalid JSON');
    }
  }

  hex4(): number {
    const h = this.s.slice(this.i, this.i + 4);
    if (!/^[0-9a-fA-F]{4}$/.test(h)) throw this.err('invalid escape');
    this.i += 4;
    return parseInt(h, 16);
  }

  string(): string {
    const s = this.s;
    this.i++;
    let out = '';
    for (;;) {
      const start = this.i;
      while (this.i < s.length) {
        const c = s.charCodeAt(this.i);
        if (c === 0x22 || c === 0x5c || c < 0x20) break;
        this.i++;
      }
      out += s.slice(start, this.i);
      if (this.i >= s.length) throw this.err('unterminated string');
      const c = s[this.i]!;
      if (c === '"') {
        this.i++;
        return out;
      }
      if (c !== '\\') throw this.err('control character in string');
      this.i++;
      const e = s[this.i++];
      switch (e) {
        case '"': out += '"'; break;
        case '\\': out += '\\'; break;
        case '/': out += '/'; break;
        case 'b': out += '\b'; break;
        case 'f': out += '\f'; break;
        case 'n': out += '\n'; break;
        case 'r': out += '\r'; break;
        case 't': out += '\t'; break;
        case 'u': {
          const hi = this.hex4();
          if (hi >= 0xd800 && hi < 0xdc00) {
            if (!s.startsWith('\\u', this.i)) throw this.err('lone surrogate');
            this.i += 2;
            const lo = this.hex4();
            if (lo < 0xdc00 || lo >= 0xe000) throw this.err('lone surrogate');
            out += String.fromCharCode(hi, lo);
          } else if (hi >= 0xdc00 && hi < 0xe000) {
            throw this.err('lone surrogate');
          } else {
            out += String.fromCharCode(hi);
          }
          break;
        }
        default:
          throw this.err('invalid escape');
      }
    }
  }

  number(): CanonValue {
    const s = this.s;
    const start = this.i;
    if (s[this.i] === '-') this.i++;
    const intStart = this.i;
    while (s[this.i]! >= '0' && s[this.i]! <= '9') this.i++;
    const n = this.i - intStart;
    if (n === 0 || (n > 1 && s[intStart] === '0')) throw this.err('invalid number', start);
    let isFloat = false;
    if (s[this.i] === '.') {
      this.i++;
      const f = this.i;
      while (s[this.i]! >= '0' && s[this.i]! <= '9') this.i++;
      if (this.i === f) throw this.err('invalid number', start);
      isFloat = true;
    }
    if (s[this.i] === 'e' || s[this.i] === 'E') {
      this.i++;
      if (s[this.i] === '+' || s[this.i] === '-') this.i++;
      const f = this.i;
      while (s[this.i]! >= '0' && s[this.i]! <= '9') this.i++;
      if (this.i === f) throw this.err('invalid number', start);
      isFloat = true;
    }
    const tok = s.slice(start, this.i);
    if (isFloat) {
      const f = Number(tok); // correctly rounded (ECMA-262 StringToNumber)
      if (!Number.isFinite(f)) throw this.err('non-finite number', start);
      return new PyFloat(f);
    }
    const asNumber = Number(tok);
    if (isSafe(asNumber)) return asNumber === 0 ? 0 : asNumber;
    return BigInt(tok);
  }
}

/** Reads an integer field (number or bigint, never a float) as a bigint. */
export function asBigInt(v: CanonValue | undefined): bigint | null {
  if (typeof v === 'bigint') return v;
  if (typeof v === 'number' && Number.isSafeInteger(v)) return BigInt(v);
  return null;
}

/** Reads a numeric field Python's `_is_num` would accept (int or float). */
export function asNumber(v: CanonValue | undefined): number | null {
  if (v instanceof PyFloat) return v.value;
  if (typeof v === 'number') return v;
  if (typeof v === 'bigint') return Number(v);
  return null;
}

export function isCanonObject(v: unknown): v is CanonObject {
  return isPlainObject(v);
}

const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, '0'));

export function toHex(bytes: Uint8Array): string {
  let s = '';
  for (const b of bytes) s += HEX[b];
  return s;
}

export function fromHex(hex: string): Uint8Array<ArrayBuffer> {
  if (!/^(?:[0-9a-f]{2})*$/.test(hex)) throw new CanonError('not lower-case hex');
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(2 * i, 2 * i + 2), 16);
  return out;
}

export async function sha256Hex(data: Uint8Array<ArrayBuffer> | string): Promise<string> {
  const bytes = typeof data === 'string' ? new TextEncoder().encode(data) : data;
  return toHex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)));
}
