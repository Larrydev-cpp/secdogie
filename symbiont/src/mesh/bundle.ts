/**
 * The join bundle an operator hands a volunteer (`secdogie-inference bundle`):
 * where the signaling gateway is, which entry nodes to dial, the trust lists,
 * and the operator's admission for this browser's DID. Nothing in it is
 * believed beyond what it can prove: the admission must verify against the
 * bundle's operator list and name this browser, and entry nodes must be on the
 * mesh list (the DTLS handshake checks them again).
 */

import { type CanonObject, asNumber, parseLossless } from '../core/canon.ts';
import { DidSet, type MeshTrust, checkAdmission, isDid, isObj } from './protocol.ts';

export const BUNDLE = 'secdogie/inference/join/v1';
const MAX_ENTRIES = 8;

export interface JoinBundle {
  readonly signalingUrl: string;
  readonly salt: string;
  readonly iceServers: RTCIceServer[];
  readonly entries: readonly string[];
  readonly trust: MeshTrust;
  readonly admission: CanonObject;
  readonly heartbeatEvery: number;
}

function dids(v: unknown, what: string): DidSet {
  if (v === undefined) return new DidSet();
  if (!Array.isArray(v) || !v.every(isDid)) throw new Error(`bundle: ${what} must be a list of did:key`);
  return new DidSet(v as string[]);
}

/** Parse and check a bundle for the browser `did`; throws with the reason. */
export async function readBundle(text: string, did: string, now: number): Promise<JoinBundle> {
  const b = parseLossless(text, { maxLength: 256 * 1024 });
  if (!isObj(b) || b['type'] !== BUNDLE) throw new Error(`bundle: not a ${BUNDLE}`);
  const url = b['signaling'];
  if (typeof url !== 'string' || !/^wss?:\/\//.test(url)) throw new Error('bundle: signaling must be a ws:// or wss:// URL');
  const t = isObj(b['trust']) ? b['trust'] : null;
  if (t === null) throw new Error('bundle: no trust lists');
  const trust: MeshTrust = {
    mesh: dids(t['mesh'], 'trust.mesh'),
    users: dids(t['users'], 'trust.users'),
    deciders: dids(t['deciders'], 'trust.deciders'),
    coordinators: dids(t['coordinators'], 'trust.coordinators'),
    brokers: dids(t['brokers'], 'trust.brokers'),
    operators: dids(t['operators'], 'trust.operators'),
  };
  const entries = b['entries'];
  if (!Array.isArray(entries) || entries.length === 0 || entries.length > MAX_ENTRIES || !entries.every(isDid)) {
    throw new Error(`bundle: entries must name 1..${MAX_ENTRIES} mesh nodes`);
  }
  for (const e of entries as string[]) if (!trust.mesh.has(e)) throw new Error('bundle: an entry node is not on the mesh list');
  const admission = b['admission'];
  if (!isObj(admission) || admission['subject'] !== did) throw new Error('bundle: the admission is not for this browser');
  const why = await checkAdmission(admission, trust, now);
  if (why) throw new Error(`bundle: admission refused: ${why}`);
  const ice = b['ice_servers'];
  const iceServers = Array.isArray(ice) ? ice.filter((u): u is string => typeof u === 'string').map((urls) => ({ urls })) : [];
  const heartbeatEvery = asNumber(b['heartbeat_every']) ?? 2;
  return {
    signalingUrl: url,
    salt: typeof b['salt'] === 'string' ? b['salt'] : '',
    iceServers,
    entries: entries as string[],
    trust,
    admission,
    heartbeatEvery: Number.isFinite(heartbeatEvery) && heartbeatEvery >= 0.1 && heartbeatEvery <= 60 ? heartbeatEvery : 2,
  };
}
