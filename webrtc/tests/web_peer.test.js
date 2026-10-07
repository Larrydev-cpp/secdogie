// Headless tests for the browser peer: WebSocket, RTCPeerConnection and the
// navigator are fakes installed before the module is imported.
//   node --test          (from webrtc/)
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

const sockets = [];
const pcs = [];

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  constructor(url) {
    this.url = String(url);
    this.readyState = FakeWebSocket.CONNECTING;
    this.sent = [];
    sockets.push(this);
  }
  send(text) {
    this.sent.push(JSON.parse(text));
  }
  close(code = 1000, reason = '') {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }
  // test helpers
  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }
  receive(msg) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
  drop(code, reason = '') {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }
}

class FakeChannel {
  constructor(label) {
    this.label = label;
    this.readyState = 'connecting';
    this.bufferedAmount = 0;
    this.sent = [];
  }
  send(data) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 'closed';
  }
  addEventListener() {}
  removeEventListener() {}
  open() {
    this.readyState = 'open';
    this.onopen?.();
  }
}

class FakePC {
  constructor(config) {
    this.config = config;
    this.localDescription = null;
    this.remoteDescription = null;
    this.signalingState = 'stable';
    this.iceConnectionState = 'new';
    this.channels = [];
    this.candidates = [];
    this.closed = false;
    pcs.push(this);
  }
  createDataChannel(label, opts) {
    const ch = new FakeChannel(label);
    ch.opts = opts;
    this.channels.push(ch);
    return ch;
  }
  async createOffer() {
    return { type: 'offer', sdp: `v=0\r\na=fingerprint:sha-256 AA:BB\r\na=pc:${pcs.indexOf(this)}\r\n` };
  }
  async createAnswer() {
    return { type: 'answer', sdp: `v=0\r\na=fingerprint:sha-256 CC:DD\r\na=pc:${pcs.indexOf(this)}\r\n` };
  }
  async setLocalDescription(d) {
    this.localDescription = d;
    if (d.type === 'offer') this.signalingState = 'have-local-offer';
  }
  async setRemoteDescription(d) {
    this.remoteDescription = d;
    this.signalingState = 'stable';
  }
  async addIceCandidate(c) {
    this.candidates.push(c);
  }
  close() {
    this.closed = true;
  }
  ice(state) {
    this.iceConnectionState = state;
    this.oniceconnectionstatechange?.();
  }
}

globalThis.WebSocket = FakeWebSocket;
globalThis.RTCPeerConnection = FakePC;
// A page that has seen no click: attaching must still work (the owner's decision).
Object.defineProperty(globalThis, 'navigator', {
  value: { userActivation: { isActive: false, hasBeenActive: false } },
  configurable: true,
});

const peer = await import('../client/web_peer.js');
const { PeerState } = peer;

const tick = () => new Promise((r) => setTimeout(r, 0));
const quiet = () => {};

afterEach(() => {
  peer.closePeerConnection();
  sockets.length = 0;
  pcs.length = 0;
});

async function joinSecond() {
  const states = [];
  const off = peer.onStateChange((state, detail, info) => states.push({ state, detail, info }));
  const started = peer.startPeerConnection({ signalingUrl: 'wss://sig.example/ws', room: 'room-1', log: quiet });
  const ws = sockets.at(-1);
  ws.open();
  ws.receive({ type: 'welcome', peerId: 'me', peers: ['node'] });
  await started;
  await tick();
  return { ws, states, off };
}

test('starts without a user gesture, and needs a room', async () => {
  await assert.rejects(() => peer.startPeerConnection({ signalingUrl: 'wss://sig.example/ws', log: quiet }), /room is required/);
  const { ws, off } = await joinSecond();
  assert.match(ws.url, /^wss:\/\/sig\.example\/ws\?room=room-1$/);
  off();
});

test('ws: is only for this machine', async () => {
  await assert.rejects(
    () => peer.startPeerConnection({ signalingUrl: 'ws://sig.example/ws', room: 'r', log: quiet }),
    /wss: URL/,
  );
  const ok = peer.startPeerConnection({ signalingUrl: 'ws://127.0.0.1:8787/ws', room: 'r', log: quiet });
  sockets.at(-1).open();
  sockets.at(-1).receive({ type: 'welcome', peerId: 'me', peers: [] });
  assert.deepEqual(await ok, { peerId: 'me', room: 'r' });
});

test('the second joiner offers on one ordered channel, with the default STUN not a third party', async () => {
  const { ws, off } = await joinSecond();
  const [pc] = pcs;
  assert.deepEqual(pc.config.iceServers, [{ urls: 'stun:stun.cloudflare.com:3478' }]);
  assert.equal(pc.channels.length, 1);
  assert.equal(pc.channels[0].label, 'secdogie-data');
  assert.deepEqual(pc.channels[0].opts, { ordered: true });
  const offer = ws.sent.find((m) => m.type === 'offer');
  assert.equal(offer.to, 'node');
  off();
});

test('descriptions() names the link; the id survives an ICE recovery and changes on a new link', async () => {
  assert.deepEqual(peer.descriptions(), { id: null, local: null, remote: null });
  const { ws, states, off } = await joinSecond();
  const [pc] = pcs;
  ws.receive({ type: 'answer', from: 'node', payload: { type: 'answer', sdp: 'v=0\r\na=fingerprint:sha-256 EE:FF\r\n' } });
  await tick();
  pc.channels[0].open();
  const first = peer.descriptions();
  assert.ok(first.id > 0);
  assert.match(first.local, /AA:BB/);
  assert.match(first.remote, /EE:FF/);
  const connected = states.filter((s) => s.state === PeerState.CONNECTED);
  assert.equal(connected.at(-1).info.linkId, first.id);

  pc.ice('disconnected');
  pc.ice('connected'); // recovered: same link
  assert.equal(peer.descriptions().id, first.id);
  assert.equal(states.at(-1).state, PeerState.CONNECTED);
  assert.equal(states.at(-1).info.linkId, first.id);

  // the node restarts and offers again: a fresh link
  ws.receive({ type: 'offer', from: 'node', payload: { type: 'offer', sdp: 'v=0\r\na=fingerprint:sha-256 99:88\r\n' } });
  await tick();
  await tick();
  const second = peer.descriptions();
  assert.notEqual(second.id, first.id);
  assert.ok(pc.closed);
  off();
});

test('ICE candidates that arrive for the current link are applied', async () => {
  const { ws, off } = await joinSecond();
  ws.receive({ type: 'answer', from: 'node', payload: { type: 'answer', sdp: 'v=0\r\n' } });
  ws.receive({ type: 'ice-candidate', from: 'node', payload: { candidate: 'candidate:1 1 udp 1 1.2.3.4 5 typ host', sdpMid: '0' } });
  ws.receive({ type: 'ice-candidate', from: 'stranger', payload: { candidate: 'candidate:2', sdpMid: '0' } });
  await tick();
  assert.equal(pcs[0].candidates.length, 1);
  off();
});

test('a full room fails with a code the caller can act on', async () => {
  const states = [];
  const off = peer.onStateChange((state, detail, info) => states.push({ state, info }));
  const started = peer.startPeerConnection({ signalingUrl: 'wss://sig.example/ws', room: 'busy-room', log: quiet });
  const ws = sockets.at(-1);
  ws.open();
  ws.receive({ type: 'error', code: 'room-full', message: 'room already has 2 peers' });
  ws.drop(4001, 'room full');
  await assert.rejects(started, (err) => err.code === 'room-full');
  assert.equal(states.at(-1).state, PeerState.FAILED);
  assert.equal(states.at(-1).info.code, 'room-full');
  off();
});

test('an idle drop and a plain close carry their own codes', async () => {
  const states = [];
  const off = peer.onStateChange((state, detail, info) => states.push({ state, info }));
  const started = peer.startPeerConnection({ signalingUrl: 'wss://sig.example/ws', room: 'r2', log: quiet });
  sockets.at(-1).open();
  sockets.at(-1).drop(4002, 'idle');
  await assert.rejects(started);
  assert.equal(states.at(-1).info.code, 'idle');
  const again = peer.startPeerConnection({ signalingUrl: 'wss://sig.example/ws', room: 'r2', log: quiet });
  sockets.at(-1).open();
  sockets.at(-1).receive({ type: 'welcome', peerId: 'me', peers: [] });
  await again;
  peer.closePeerConnection();
  assert.equal(states.at(-1).state, PeerState.IDLE);
  assert.equal(states.at(-1).info.code, 'closed');
  off();
});
