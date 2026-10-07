// ATTACH_OPERATOR with a fake web_peer.js and a scripted node: no network
// without a pairing; W1 before anything is revealed; refusals that count only
// when the node signed them; the two-sided pairing; the newest tab wins.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { type CanonObject, canonicalText, parseLossless } from '../src/core/canon.ts';
import { WebCryptoSigner } from '../src/core/ed25519.ts';
import { captureTrace } from '../src/core/trace.ts';
import { Attachment, type BroadcastLike, type LinkPhase } from '../src/net/attach.ts';
import { Keystore, MemoryKv, type PairingRecord } from '../src/net/keystore.ts';
import * as la from '../src/net/linkauth.ts';
import type { PeerApi, PeerInfo, PeerStateName } from '../src/net/peer_api.ts';

const fp = (alg: string, byte: string, n: number) => `${alg} ${Array(n).fill(byte).join(':')}`;
const PAGE_FP = fp('sha-256', 'AA', 32);
const NODE_FPS = [fp('sha-256', 'BB', 32), fp('sha-384', 'BC', 48), fp('sha-512', 'BD', 64)];
const sdp = (fps: string[], media = 'm=application 9 UDP/DTLS/SCTP webrtc-datachannel') =>
  ['v=0', media, ...fps.map((f) => `a=fingerprint:${f.split(' ')[0]} ${f.split(' ')[1]}`)].join('\r\n') + '\r\n';

class FakePeer implements PeerApi {
  starts: Array<{ signalingUrl: string; room: string }> = [];
  sent: Array<string | Uint8Array> = [];
  closes = 0;
  desc = { id: null as number | null, local: null as string | null, remote: null as string | null };
  #msg = new Set<(d: string | ArrayBuffer) => void>();
  #state = new Set<(s: PeerStateName, d: string, i: PeerInfo) => void>();

  async startPeerConnection(o: { signalingUrl: string; room: string }) {
    this.starts.push({ signalingUrl: o.signalingUrl, room: o.room });
    return { peerId: 'me', room: o.room };
  }
  closePeerConnection() {
    this.closes += 1;
  }
  async sendData(p: string | ArrayBuffer | Uint8Array) {
    this.sent.push(typeof p === 'string' ? p : new Uint8Array(p as ArrayBuffer));
  }
  descriptions() {
    return this.desc;
  }
  onMessage(cb: (d: string | ArrayBuffer) => void) {
    this.#msg.add(cb);
    return () => this.#msg.delete(cb);
  }
  onStateChange(cb: (s: PeerStateName, d: string, i: PeerInfo) => void) {
    this.#state.add(cb);
    return () => this.#state.delete(cb);
  }
  // test side
  connect(id: number, remote = sdp(NODE_FPS.slice(0, 1))) {
    this.desc = { id, local: sdp([PAGE_FP]), remote };
    for (const cb of this.#state) cb('connected', 'open', { code: null, linkId: id });
  }
  state(s: PeerStateName, code: string | null = null) {
    for (const cb of this.#state) cb(s, '', { code, linkId: this.desc.id });
  }
  deliver(d: string | ArrayBuffer) {
    for (const cb of this.#msg) cb(d);
  }
  texts(): CanonObject[] {
    return this.sent.filter((x): x is string => typeof x === 'string').map((t) => parseLossless(t) as CanonObject);
  }
}

class FakeTimers {
  now = 1_760_000_000;
  #q: Array<{ at: number; fn: () => void; id: number }> = [];
  #id = 0;
  set = (fn: () => void, ms: number) => {
    const id = ++this.#id;
    this.#q.push({ at: this.now * 1000 + ms, fn, id });
    return id;
  };
  clear = (h: unknown) => {
    this.#q = this.#q.filter((t) => t.id !== h);
  };
  advance(ms: number) {
    const end = this.now * 1000 + ms;
    for (;;) {
      this.#q.sort((a, b) => a.at - b.at);
      const t = this.#q[0];
      if (!t || t.at > end) break;
      this.#q.shift();
      this.now = t.at / 1000;
      t.fn();
    }
    this.now = end / 1000;
  }
}

const settle = () => new Promise((r) => setTimeout(r, 5));

async function world(opts: { paired?: boolean; hash?: string; operatorEnrolled?: boolean; kv?: MemoryKv } = {}) {
  const node = await WebCryptoSigner.generate();
  const kv = opts.kv ?? new MemoryKv();
  const keystore = new Keystore(kv);
  const keys = await keystore.keys();
  const room = 'standing-room-0001';
  if (opts.paired !== false && !opts.hash) {
    const rec: PairingRecord = { v: 1, node: node.did, room, app: keys.app.did, operator: opts.operatorEnrolled ? keys.operator.did : '', pairedAt: 1 };
    await keystore.savePairing(rec);
  }
  const peer = new FakePeer();
  const timers = new FakeTimers();
  const phases: Array<[LinkPhase, string | null]> = [];
  let cleared = false;
  let active = false;
  const att = new Attachment({
    peer, keystore, config: { signalUrl: 'wss://gateway.example/ws', iceServers: [] }, hash: opts.hash ?? '',
    clearHash: () => (cleared = true), now: () => timers.now, random: () => 0.5, timers, tabs: null,
    activation: () => active,
  });
  att.onPhase((p, i) => phases.push([p, i.reason]));
  const nodeSays = async (obj: CanonObject) => {
    peer.deliver(canonicalText(obj));
    await settle();
  };
  const nodeBinding = (signer = node, local = NODE_FPS, remote = [PAGE_FP], r = room) =>
    la.createLinkBinding(signer, { room: r, local, remote, issuedAt: timers.now });
  return { node, kv, keystore, keys, room, peer, timers, phases, att, nodeSays, nodeBinding,
    wasCleared: () => cleared, setActive: (v: boolean) => (active = v) };
}

test('no pairing and no link: nothing leaves the page', async () => {
  const w = await world({ paired: false });
  await w.att.start();
  assert.equal(w.att.phase, 'unpaired');
  assert.equal(w.peer.starts.length, 0);
});

test('paired: joins the standing room on the build-time gateway, reveals nothing until W1 verifies', async () => {
  const w = await world();
  await w.att.start();
  assert.deepEqual(w.peer.starts, [{ signalingUrl: 'wss://gateway.example/ws', room: w.room }]);
  w.peer.connect(1);
  await settle();
  assert.equal(w.peer.sent.length, 0, 'the page speaks only after the node');
  const bound: number[] = [];
  w.att.onBound((id) => bound.push(id));
  await w.nodeSays(await w.nodeBinding());
  const [mine] = w.peer.texts();
  assert.equal(mine!['type'], la.LINK_BINDING_TYPE);
  assert.equal(mine!['did'], w.keys.app.did);
  assert.equal(w.att.phase, 'connected');
  assert.deepEqual(bound, [1]);
  // frames flow only now
  const frames: Uint8Array[] = [];
  w.att.onFrame((f) => frames.push(f));
  w.peer.deliver(new Uint8Array([1, 2, 3]).buffer);
  assert.deepEqual(frames.map((f) => [...f]), [[1, 2, 3]]);
  assert.ok(await w.att.send(new Uint8Array([9])));
});

test('frames before W1 are dropped, and a node that is not the paired one learns nothing', async () => {
  const w = await world();
  await w.att.start();
  w.peer.connect(1);
  const frames: Uint8Array[] = [];
  w.att.onFrame((f) => frames.push(f));
  w.peer.deliver(new Uint8Array([1]).buffer);
  assert.equal(frames.length, 0);
  assert.equal(await w.att.send(new Uint8Array([1])), false);
  const impostor = await WebCryptoSigner.generate();
  await w.nodeSays(await w.nodeBinding(impostor));
  assert.equal(w.peer.sent.length, 0, 'nothing about this browser was said');
  assert.ok(w.peer.closes >= 1);
});

test('a gateway in the middle fails W1; three failures in a row stop trying', async () => {
  const w = await world();
  await w.att.start();
  for (let i = 1; i <= 3; i++) {
    w.peer.connect(i, sdp([fp('sha-256', 'EE', 32)])); // the page sees a relay's certificate
    await w.nodeSays(await w.nodeBinding());
    assert.equal(w.peer.sent.length, 0);
    w.timers.advance(70_000);
  }
  assert.equal(w.att.phase, 'refused');
  assert.equal(w.att.info.reason, 'w1-failed');
});

test('an offer with media is refused before W1', async () => {
  const w = await world();
  await w.att.start();
  w.peer.connect(1, sdp(NODE_FPS.slice(0, 1)) + 'm=video 9 UDP/TLS/RTP/SAVPF 96\r\n');
  await w.nodeSays(await w.nodeBinding());
  assert.equal(w.peer.sent.length, 0);
  assert.ok(w.peer.closes >= 1);
});

test('the node saying "not enrolled" makes the page forget its pairing -- only when the node signed it', async () => {
  const w = await world();
  await w.att.start();
  w.peer.connect(1);
  await w.nodeSays(await w.nodeBinding());
  const forged = await la.createLinkBinding(await WebCryptoSigner.generate(), { room: w.room, local: NODE_FPS, remote: [PAGE_FP], issuedAt: w.timers.now });
  await w.nodeSays({ ...forged, type: la.REFUSED_TYPE });
  assert.ok(await w.keystore.pairing(), 'an unsigned refusal changes nothing');
  const refusal = await (async () => {
    const { signPayload } = await import('../src/core/envelope.ts');
    const { pyFloat } = await import('../src/core/canon.ts');
    return signPayload(w.node, {
      type: la.REFUSED_TYPE, did: w.node.did, reason: 'not-enrolled', pairing_id: '',
      local_fingerprints: NODE_FPS, remote_fingerprints: [PAGE_FP], issued_at: pyFloat(w.timers.now),
    });
  })();
  await w.nodeSays(refusal);
  assert.equal(w.att.phase, 'unpaired');
  assert.equal(w.att.info.reason, 'not-enrolled');
  assert.equal(await w.keystore.pairing(), null);
});

test('a room that stays full becomes "busy" (unverified); a moment of it is just reconnecting', async () => {
  const w = await world();
  await w.att.start();
  w.peer.state('failed', 'room-full');
  assert.notEqual(w.att.phase, 'busy');
  w.timers.advance(5_000);
  w.peer.state('failed', 'room-full');
  assert.notEqual(w.att.phase, 'busy');
  w.timers.advance(6_000);
  w.peer.state('failed', 'room-full');
  assert.equal(w.att.phase, 'busy');
  assert.equal(w.att.info.unverified, true);
  assert.ok(w.peer.starts.length >= 2, 'and it keeps trying');
});

test('a node nobody hears from is "unreachable", and the page keeps trying', async () => {
  const w = await world();
  await w.att.start();
  w.timers.advance(9_000);
  assert.equal(w.att.phase, 'unreachable');
  w.peer.state('failed', 'signal-closed');
  w.timers.advance(2_000);
  assert.ok(w.peer.starts.length >= 2);
});

test('an ICE recovery on the same link does not ask the node again', async () => {
  const w = await world();
  await w.att.start();
  w.peer.connect(1);
  await w.nodeSays(await w.nodeBinding());
  w.peer.state('disconnected');
  assert.equal(w.att.phase, 'reconnecting');
  w.peer.connect(1);
  await settle();
  assert.equal(w.att.phase, 'connected');
  assert.equal(w.peer.texts().length, 1);
});

test('a paired browser ignores a pairing link and strips it from the address bar', async () => {
  const w = await world();
  const secret = crypto.getRandomValues(new Uint8Array(32));
  const body = { v: 1, node: (await WebCryptoSigner.generate()).did, secret: la.b64url(secret), exp: 1_760_000_600 };
  const link = '#pair=' + la.b64url(new TextEncoder().encode(canonicalText(body)));
  const att = new Attachment({
    peer: w.peer, keystore: w.keystore, config: { signalUrl: 'wss://gateway.example/ws', iceServers: [] }, hash: link,
    clearHash: () => undefined, now: () => w.timers.now, timers: w.timers, tabs: null,
  });
  await att.start();
  assert.equal(w.peer.starts.at(-1)!.room, w.room, 'still the paired node, not the link');
});

test('pairing: check the node, show the code, tap, receipt, then attach for real', async () => {
  const node = await WebCryptoSigner.generate();
  const secret = crypto.getRandomValues(new Uint8Array(32));
  const timersNow = 1_760_000_000;
  const body = { v: 1, node: node.did, secret: la.b64url(secret), exp: timersNow + 600 };
  const hash = '#pair=' + la.b64url(new TextEncoder().encode(canonicalText(body)));
  const w = await world({ hash }); // the scripted node here is `node`, the one the link names
  await w.att.start();
  assert.ok(w.wasCleared(), 'the link (a key) leaves the address bar');
  const invite = await la.parsePairingFragment(hash, timersNow);
  assert.equal(w.peer.starts[0]!.room, invite.room);
  assert.equal(w.att.phase, 'pairing');
  assert.equal(w.att.pairing?.stage, 'checking');
  w.peer.connect(1);
  await w.nodeSays(await la.createLinkBinding(node, { room: invite.room, local: NODE_FPS, remote: [PAGE_FP], issuedAt: w.timers.now }));
  const [hello] = w.peer.texts();
  assert.equal(hello!['type'], la.PAIR_HELLO_TYPE);
  assert.equal(hello!['operator'], w.keys.operator.did, 'the operator key is offered; the owner decides');
  assert.equal(w.att.pairing?.stage, 'confirm');
  assert.equal(w.att.pairing?.code, await la.checkCode(hello!));
  // a tap that is not a real user activation does nothing
  w.setActive(false);
  assert.equal(await w.att.confirmPairing(), false);
  w.setActive(true);
  assert.equal(await w.att.confirmPairing(), true);
  const confirm = w.peer.texts()[1]!;
  assert.equal(confirm['type'], la.PAIR_CONFIRM_TYPE);
  assert.equal((confirm['operator_proof'] as CanonObject)['did'], w.keys.operator.did);
  assert.equal(await w.keystore.pairing(), null, 'nothing is stored before the receipt');
  const { signPayload } = await import('../src/core/envelope.ts');
  const { pyFloat } = await import('../src/core/canon.ts');
  await w.nodeSays(await signPayload(node, {
    type: la.PAIRED_TYPE, did: node.did, app: w.keys.app.did, operator: w.keys.operator.did, room: 'the-standing-room',
    pairing_id: invite.pairingId, local_fingerprints: NODE_FPS, remote_fingerprints: [PAGE_FP], issued_at: pyFloat(w.timers.now),
  }));
  const rec = await w.keystore.pairing();
  assert.equal(rec?.room, 'the-standing-room');
  assert.equal(rec?.node, node.did);
  assert.ok(w.att.canApprove);
  assert.equal(w.peer.starts.at(-1)!.room, 'the-standing-room');
});

test('a pairing link that names a gateway is refused before anything connects', async () => {
  const body = { v: 1, node: (await WebCryptoSigner.generate()).did, secret: la.b64url(new Uint8Array(32)), exp: 1_760_000_600, signal: 'wss://evil.example/ws' };
  const w = await world({ hash: '#pair=' + la.b64url(new TextEncoder().encode(JSON.stringify(body))) });
  await w.att.start();
  assert.equal(w.att.phase, 'unpaired');
  assert.equal(w.att.info.reason, 'bad-link');
  assert.equal(w.peer.starts.length, 0);
});

test('keys that cannot be read are never replaced', async () => {
  const kv = new MemoryKv();
  const ks = new Keystore(kv);
  const before = await ks.keys();
  kv.failReads = true;
  const peer = new FakePeer();
  const att = new Attachment({ peer, keystore: ks, config: { signalUrl: 'wss://g/ws', iceServers: [] }, hash: '', tabs: null });
  await att.start();
  assert.equal(att.phase, 'unreachable');
  assert.equal(att.info.reason, 'keys-unreadable');
  kv.failReads = false;
  assert.equal((await ks.keys()).app.did, before.app.did, 'the same keys, still');
  assert.equal(peer.starts.length, 0);
});

test('the newest tab takes over; the older one steps aside for good', async () => {
  class Bus {
    static all: Bus[] = [];
    onmessage: ((ev: { data: unknown }) => void) | null = null;
    constructor() {
      Bus.all.push(this);
    }
    postMessage(data: unknown) {
      for (const b of Bus.all) if (b !== this) b.onmessage?.({ data });
    }
    close() {}
  }
  const w = await world();
  const mk = (peer: FakePeer) => new Attachment({
    peer, keystore: w.keystore, config: { signalUrl: 'wss://g/ws', iceServers: [] }, hash: '', tabs: new Bus() as BroadcastLike,
    now: () => w.timers.now, timers: w.timers,
  });
  const older = mk(new FakePeer());
  await older.start();
  const newer = mk(new FakePeer());
  await newer.start();
  assert.equal(older.phase, 'busy');
  assert.equal(older.info.reason, 'another-tab');
  assert.notEqual(newer.phase, 'busy');
});

test('DevTools never sees the pairing secret, the link, or what anyone typed', async () => {
  const lines: string[] = [];
  const restore = captureTrace((line, fields) => lines.push(line + ' ' + JSON.stringify(fields, (_k, v) => (typeof v === 'bigint' ? String(v) : v))));
  try {
    const node = await WebCryptoSigner.generate();
    const secret = crypto.getRandomValues(new Uint8Array(32));
    const body = { v: 1, node: node.did, secret: la.b64url(secret), exp: 1_760_000_600 };
    const frag = la.b64url(new TextEncoder().encode(canonicalText(body)));
    const w = await world({ hash: '#pair=' + frag });
    await w.att.start();
    const invite = await la.parsePairingFragment('#pair=' + frag, 1_760_000_000);
    w.peer.connect(1);
    await w.nodeSays(await la.createLinkBinding(node, { room: invite.room, local: NODE_FPS, remote: [PAGE_FP], issuedAt: w.timers.now }));
    w.setActive(true);
    await w.att.confirmPairing();
    const all = lines.join('\n');
    assert.ok(lines.some((l) => l.includes('ATTACH_OPERATOR')));
    assert.ok(!all.includes(la.b64url(secret)), 'secret');
    assert.ok(!all.includes(frag), 'link');
    for (const t of w.peer.texts()) if (typeof t['mac'] === 'string') assert.ok(!all.includes(t['mac'] as string), 'mac');
  } finally {
    restore();
  }
});
