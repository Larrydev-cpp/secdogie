/**
 * What this browser keeps, and nothing more: two non-extractable Ed25519 keys
 * (the App key that speaks for this page, and the operator key that can sign
 * Gate 2 only if the owner allowed it at pairing) and the pairing record (which
 * node, which room). No conversation, no goals, no answers are ever stored.
 *
 * The keys are CryptoKey objects in IndexedDB: the page can use them but no
 * script can read them out. That stops copying a key, not using it -- code
 * running on this page's origin acts as this page (SECURITY.md).
 *
 * A read that fails is not the same as nothing stored: on an error this module
 * throws and never generates over what might still be there.
 *
 * This is the only file allowed to touch IndexedDB (tests/purity.test.ts).
 */

import { isDidKey } from '../core/did.ts';
import { WebCryptoSigner } from '../core/ed25519.ts';

export type StoreName = 'keys' | 'pairing';

export interface KvStore {
  /** `undefined` when nothing is stored; throws when the store cannot be read. */
  get(store: StoreName, key: string): Promise<unknown>;
  put(store: StoreName, key: string, value: unknown): Promise<void>;
  delete(store: StoreName, key: string): Promise<void>;
}

export class KeystoreError extends Error {}

export interface Keys {
  readonly app: WebCryptoSigner;
  readonly operator: WebCryptoSigner;
}

export interface PairingRecord {
  readonly v: 1;
  /** The node this browser was paired with. */
  readonly node: string;
  /** Its standing signaling room (from the node's signed receipt). */
  readonly room: string;
  readonly app: string;
  /** The operator DID the node enrolled, or '' when the owner did not allow Gate 2 from here. */
  readonly operator: string;
  readonly pairedAt: number;
}

const KEYS = 'identity/v1';
const PAIRING = 'node/v1';
const ROOM = /^[A-Za-z0-9_-]{1,64}$/;

export class Keystore {
  readonly #kv: KvStore;

  constructor(kv: KvStore) {
    this.#kv = kv;
  }

  /** The two keys -- created on first use, restored and self-checked after. */
  async keys(): Promise<Keys> {
    let raw: unknown;
    try {
      raw = await this.#kv.get('keys', KEYS);
    } catch (e) {
      throw new KeystoreError(`the key store cannot be read: ${(e as Error).message}`);
    }
    if (raw === undefined) {
      const app = await WebCryptoSigner.generateWithKey();
      const operator = await WebCryptoSigner.generateWithKey();
      await this.#kv.put('keys', KEYS, {
        v: 1,
        app: { did: app.signer.did, key: app.key },
        operator: { did: operator.signer.did, key: operator.key },
        createdAt: Date.now(),
      });
      return { app: app.signer, operator: operator.signer };
    }
    const r = raw as { v?: unknown; app?: { did?: unknown; key?: unknown }; operator?: { did?: unknown; key?: unknown } };
    if (r?.v !== 1 || typeof r.app?.did !== 'string' || typeof r.operator?.did !== 'string') {
      throw new KeystoreError('the stored keys are damaged');
    }
    try {
      return {
        app: await WebCryptoSigner.restore(r.app.did, r.app.key as CryptoKey),
        operator: await WebCryptoSigner.restore(r.operator.did, r.operator.key as CryptoKey),
      };
    } catch (e) {
      throw new KeystoreError(`the stored keys do not check out: ${(e as Error).message}`);
    }
  }

  async pairing(): Promise<PairingRecord | null> {
    const r = (await this.#kv.get('pairing', PAIRING)) as Partial<PairingRecord> | undefined;
    if (r === undefined) return null;
    const ok =
      r?.v === 1 && typeof r.node === 'string' && isDidKey(r.node) && typeof r.room === 'string' && ROOM.test(r.room) &&
      typeof r.app === 'string' && typeof r.operator === 'string' && typeof r.pairedAt === 'number';
    return ok ? (r as PairingRecord) : null;
  }

  /** Called only after the node's signed receipt verified. */
  async savePairing(record: PairingRecord): Promise<void> {
    await this.#kv.put('pairing', PAIRING, { ...record });
  }

  /** Called only on a node-signed "not enrolled" for this very link. */
  async forgetPairing(): Promise<void> {
    await this.#kv.delete('pairing', PAIRING);
  }
}

/** The browser's store. Opening it creates the two object stores and nothing else. */
export function indexedDbStore(name = 'secdogie-symbiont'): KvStore {
  let opened: Promise<IDBDatabase> | null = null;
  const db = (): Promise<IDBDatabase> =>
    (opened ??= new Promise((resolve, reject) => {
      const req = indexedDB.open(name, 1);
      req.onupgradeneeded = () => {
        req.result.createObjectStore('keys');
        req.result.createObjectStore('pairing');
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error ?? new Error('IndexedDB open failed'));
      req.onblocked = () => reject(new Error('IndexedDB is blocked by another tab'));
    }));
  const run = async <T>(store: StoreName, mode: IDBTransactionMode, op: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
    const d = await db();
    return new Promise<T>((resolve, reject) => {
      const tx = d.transaction(store, mode);
      const req = op(tx.objectStore(store));
      tx.oncomplete = () => resolve(req.result);
      tx.onerror = () => reject(tx.error ?? new Error('IndexedDB transaction failed'));
      tx.onabort = () => reject(tx.error ?? new Error('IndexedDB transaction aborted'));
    });
  };
  return {
    get: (store, key) => run(store, 'readonly', (s) => s.get(key)),
    put: async (store, key, value) => {
      await run(store, 'readwrite', (s) => s.put(value, key));
    },
    delete: async (store, key) => {
      await run(store, 'readwrite', (s) => s.delete(key));
    },
  };
}

/** In memory: the demo (which never touches the real store) and tests. */
export class MemoryKv implements KvStore {
  readonly data = new Map<string, unknown>();
  failReads = false;

  async get(store: StoreName, key: string): Promise<unknown> {
    if (this.failReads) throw new Error('read failed');
    return this.data.get(`${store}/${key}`);
  }

  async put(store: StoreName, key: string, value: unknown): Promise<void> {
    this.data.set(`${store}/${key}`, value);
  }

  async delete(store: StoreName, key: string): Promise<void> {
    this.data.delete(`${store}/${key}`);
  }
}
