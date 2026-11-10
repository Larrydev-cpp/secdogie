// Where a browser keeps its own device key and what pairing produced.
//
// The key pair is generated non-extractable and stored in IndexedDB as a
// CryptoKey: IndexedDB clones it without ever exporting the private key, so
// the key cannot leave this browser, not even through this module. Nothing
// here talks to the network.
//
// Storage can be missing or refused (a private window, blocked site data):
// every call then falls back to memory for this page's lifetime, and
// `remembered` says so, so the page can tell the person nothing will be kept.

import { generateIdentity, identityFromKeyPair } from './pairing.js';

const DB_NAME = 'secdogie-pairing';
const STORE = 'items';
const memory = new Map(); // the fallback, and what this page has already loaded

let opening = null;

function openDb() {
  if (!opening) {
    opening = new Promise((resolve) => {
      try {
        const req = globalThis.indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => req.result.createObjectStore(STORE);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null);
        req.onblocked = () => resolve(null);
      } catch {
        resolve(null);
      }
    });
  }
  return opening;
}

function run(db, mode, act) {
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, mode);
      const req = act(tx.objectStore(STORE));
      tx.oncomplete = () => resolve({ ok: true, value: req?.result });
      tx.onerror = () => resolve({ ok: false });
      tx.onabort = () => resolve({ ok: false });
    } catch {
      resolve({ ok: false });
    }
  });
}

async function get(key) {
  const db = await openDb();
  if (db) {
    const r = await run(db, 'readonly', (s) => s.get(key));
    if (r.ok && r.value !== undefined) return r.value;
  }
  return memory.get(key);
}

/** Stores a value; resolves true when it will outlive this page. */
async function put(key, value) {
  memory.set(key, value);
  const db = await openDb();
  return db ? (await run(db, 'readwrite', (s) => s.put(value, key))).ok : false;
}

async function del(key) {
  memory.delete(key);
  const db = await openDb();
  if (db) await run(db, 'readwrite', (s) => s.delete(key));
}

async function entries(prefix) {
  const db = await openDb();
  const found = new Map([...memory].filter(([k]) => k.startsWith(prefix)));
  if (db) {
    const range = IDBKeyRange.bound(prefix, `${prefix}￿`);
    const keys = await run(db, 'readonly', (s) => s.getAllKeys(range));
    const values = await run(db, 'readonly', (s) => s.getAll(range));
    if (keys.ok && values.ok) keys.value.forEach((k, i) => found.set(k, values.value[i]));
  }
  return [...found.values()];
}

/**
 * This browser's identity for `slot` ('owner' or 'device'), created on first
 * use. Resolves { identity, remembered }.
 */
export async function loadOrCreateIdentity(slot) {
  const key = `key:${slot}`;
  const held = await get(key);
  if (held?.publicKey && held?.privateKey) {
    return { identity: await identityFromKeyPair(held), remembered: true };
  }
  const identity = await generateIdentity();
  const remembered = await put(key, identity.keyPair);
  return { identity, remembered };
}

/** The identity for `slot` if one exists, without creating it. */
export async function existingIdentity(slot) {
  const held = await get(`key:${slot}`);
  return held?.publicKey && held?.privateKey ? identityFromKeyPair(held) : null;
}

/** Forgets the key for `slot` and everything recorded under it. */
export async function forgetIdentity(slot) {
  await del(`key:${slot}`);
  if (slot === 'owner') {
    for (const item of await entries('device:')) await del(`device:${item.device}`);
  } else {
    for (const item of await entries('membership:')) await del(`membership:${item.mesh}`);
  }
}

/** The inviting side records a device it admitted (the signed admission itself). */
export function saveDevice(admission) {
  return put(`device:${admission.device}`, admission);
}

/** The devices this browser has admitted, newest first. */
export async function listDevices() {
  return (await entries('device:')).sort((a, b) => b.admitted_at - a.admitted_at);
}

/** The joining side keeps the admission it received, one per mesh. */
export function saveMembership(admission) {
  return put(`membership:${admission.mesh}`, admission);
}

export function listMemberships() {
  return entries('membership:');
}
