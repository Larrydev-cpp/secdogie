/**
 * BLAKE2b-512 (RFC 7693), unkeyed -- the hash inside libsodium's `crypto_kx`,
 * which WebCrypto does not offer. Checked against Python's `hashlib.blake2b`
 * for inputs of 0 to 257 bytes (fixtures/vectors/symbiont_link.json).
 *
 * 64-bit words are kept as pairs of 32-bit halves in Uint32Arrays (low half
 * first). Every working buffer is zeroed before {@link Blake2b512.digest}
 * returns. No constant-time claim is made: the one secret input on this page
 * is a single 96-byte crypto_kx transcript per link.
 */

const IV32 = Uint32Array.from([
  0xf3bcc908, 0x6a09e667, 0x84caa73b, 0xbb67ae85, 0xfe94f82b, 0x3c6ef372, 0x5f1d36f1, 0xa54ff53a,
  0xade682d1, 0x510e527f, 0x2b3e6c1f, 0x9b05688c, 0xfb41bd6b, 0x1f83d9ab, 0x137e2179, 0x5be0cd19,
]);

const SIGMA = [
  0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
  14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3,
  11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4,
  7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8,
  9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13,
  2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9,
  12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11,
  13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10,
  6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5,
  10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0,
  0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
  14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3,
];

/** The same schedule, as indices into the 32-bit message array. */
const SIGMA2 = Uint8Array.from(SIGMA.map((x) => x * 2));

const OUT_BYTES = 64;
const BLOCK = 128;

export class Blake2b512 {
  readonly #h = new Uint32Array(16);
  readonly #v = new Uint32Array(32);
  readonly #m = new Uint32Array(32);
  readonly #block = new Uint8Array(BLOCK);
  #fill = 0;
  /** Bytes compressed so far (inputs here never reach 2**53). */
  #t = 0;
  #done = false;

  constructor() {
    this.#h.set(IV32);
    // parameter block: digest length 64, no key, fanout 1, depth 1
    this.#h[0] = this.#h[0]! ^ 0x01010000 ^ OUT_BYTES;
  }

  update(data: Uint8Array): this {
    if (this.#done) throw new Error('blake2b: already finished');
    for (const byte of data) {
      if (this.#fill === BLOCK) {
        this.#t += BLOCK;
        this.#compress(false);
        this.#fill = 0;
      }
      this.#block[this.#fill++] = byte;
    }
    return this;
  }

  /** The 64-byte digest. The state is wiped and the object cannot be reused. */
  digest(): Uint8Array<ArrayBuffer> {
    if (this.#done) throw new Error('blake2b: already finished');
    this.#done = true;
    this.#t += this.#fill;
    this.#block.fill(0, this.#fill);
    this.#compress(true);
    const out = new Uint8Array(OUT_BYTES);
    for (let i = 0; i < OUT_BYTES; i++) out[i] = this.#h[i >> 2]! >>> (8 * (i & 3));
    this.#h.fill(0);
    this.#v.fill(0);
    this.#m.fill(0);
    this.#block.fill(0);
    return out;
  }

  #compress(last: boolean): void {
    const v = this.#v;
    const m = this.#m;
    const h = this.#h;
    const b = this.#block;
    for (let i = 0; i < 16; i++) {
      v[i] = h[i]!;
      v[i + 16] = IV32[i]!;
    }
    v[24] = v[24]! ^ (this.#t >>> 0);
    v[25] = v[25]! ^ Math.floor(this.#t / 0x100000000);
    if (last) {
      v[28] = ~v[28]!;
      v[29] = ~v[29]!;
    }
    for (let i = 0; i < 32; i++) {
      m[i] = b[4 * i]! | (b[4 * i + 1]! << 8) | (b[4 * i + 2]! << 16) | (b[4 * i + 3]! << 24);
    }
    for (let r = 0; r < 12; r++) {
      const s = r * 16;
      g(v, m, 0, 8, 16, 24, SIGMA2[s]!, SIGMA2[s + 1]!);
      g(v, m, 2, 10, 18, 26, SIGMA2[s + 2]!, SIGMA2[s + 3]!);
      g(v, m, 4, 12, 20, 28, SIGMA2[s + 4]!, SIGMA2[s + 5]!);
      g(v, m, 6, 14, 22, 30, SIGMA2[s + 6]!, SIGMA2[s + 7]!);
      g(v, m, 0, 10, 20, 30, SIGMA2[s + 8]!, SIGMA2[s + 9]!);
      g(v, m, 2, 12, 22, 24, SIGMA2[s + 10]!, SIGMA2[s + 11]!);
      g(v, m, 4, 14, 16, 26, SIGMA2[s + 12]!, SIGMA2[s + 13]!);
      g(v, m, 6, 8, 18, 28, SIGMA2[s + 14]!, SIGMA2[s + 15]!);
    }
    for (let i = 0; i < 16; i++) h[i] = h[i]! ^ v[i]! ^ v[i + 16]!;
  }
}

/** v[a] += v[b] (64-bit). */
function add(v: Uint32Array, a: number, b: number): void {
  const lo = v[a]! + v[b]!;
  v[a + 1] = v[a + 1]! + v[b + 1]! + (lo >= 0x100000000 ? 1 : 0);
  v[a] = lo;
}

/** v[a] += (lo, hi) (64-bit). */
function addWord(v: Uint32Array, a: number, lo: number, hi: number): void {
  const l = v[a]! + lo;
  v[a + 1] = v[a + 1]! + hi + (l >= 0x100000000 ? 1 : 0);
  v[a] = l;
}

/** The G mixing function, rotations 32, 24, 16, 63. */
function g(v: Uint32Array, m: Uint32Array, a: number, b: number, c: number, d: number, ix: number, iy: number): void {
  add(v, a, b);
  addWord(v, a, m[ix]!, m[ix + 1]!);
  let x0 = v[d]! ^ v[a]!;
  let x1 = v[d + 1]! ^ v[a + 1]!;
  v[d] = x1;
  v[d + 1] = x0;
  add(v, c, d);
  x0 = v[b]! ^ v[c]!;
  x1 = v[b + 1]! ^ v[c + 1]!;
  v[b] = (x0 >>> 24) ^ (x1 << 8);
  v[b + 1] = (x1 >>> 24) ^ (x0 << 8);
  add(v, a, b);
  addWord(v, a, m[iy]!, m[iy + 1]!);
  x0 = v[d]! ^ v[a]!;
  x1 = v[d + 1]! ^ v[a + 1]!;
  v[d] = (x0 >>> 16) ^ (x1 << 16);
  v[d + 1] = (x1 >>> 16) ^ (x0 << 16);
  add(v, c, d);
  x0 = v[b]! ^ v[c]!;
  x1 = v[b + 1]! ^ v[c + 1]!;
  v[b] = (x1 >>> 31) ^ (x0 << 1);
  v[b + 1] = (x0 >>> 31) ^ (x1 << 1);
}

/** BLAKE2b-512 of the concatenation of `parts`. */
export function blake2b512(...parts: Uint8Array[]): Uint8Array<ArrayBuffer> {
  const h = new Blake2b512();
  for (const p of parts) h.update(p);
  return h.digest();
}
