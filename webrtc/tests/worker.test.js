// Headless tests for the signaling gateway: the Durable Object is driven with
// fake sockets and a fake clock, so no Cloudflare runtime is needed.
//   node --test          (from webrtc/)
import assert from 'node:assert/strict';
import { test } from 'node:test';

import worker, { SignalingRoom } from '../signaling/worker.js';

const { LIMITS } = SignalingRoom;

class FakeSocket {
  constructor() {
    this.sent = [];
    this.closed = null;
    this.listeners = {};
  }
  accept() {}
  addEventListener(type, fn) {
    (this.listeners[type] ??= []).push(fn);
  }
  send(data) {
    if (this.closed) throw new Error('socket is closed');
    this.sent.push(JSON.parse(data));
  }
  close(code, reason) {
    this.closed ??= { code, reason };
  }
  receive(msg) {
    const data = typeof msg === 'string' || msg instanceof ArrayBuffer ? msg : JSON.stringify(msg);
    for (const fn of this.listeners.message ?? []) fn({ data });
  }
  hangUp() {
    this.closed ??= { code: 1000, reason: 'client' };
    for (const fn of this.listeners.close ?? []) fn({ code: 1000, reason: 'client' });
  }
  last() {
    return this.sent.at(-1);
  }
}

class FakeStorage {
  alarm = null;
  async getAlarm() {
    return this.alarm;
  }
  async setAlarm(time) {
    this.alarm = time;
  }
}

function makeRoom() {
  const clock = { now: 1_000_000 };
  const storage = new FakeStorage();
  const room = new SignalingRoom({ storage }, {}, () => clock.now);
  return { room, clock, storage };
}

function pair() {
  const ctx = makeRoom();
  const a = new FakeSocket();
  const b = new FakeSocket();
  const sa = ctx.room.join(a);
  const sb = ctx.room.join(b);
  return { ...ctx, a, b, idA: sa.id, idB: sb.id };
}

const offer = { type: 'offer', sdp: 'v=0\r\n' };

test('welcome lists present peers and the room is told about joins', () => {
  const { a, b, idA, idB } = pair();
  assert.deepEqual(a.sent[0], { type: 'welcome', peerId: idA, peers: [] });
  assert.deepEqual(b.sent[0], { type: 'welcome', peerId: idB, peers: [idA] });
  assert.deepEqual(a.last(), { type: 'peer-joined', peerId: idB });
});

test('relays the three signaling types to the addressed peer with a gateway-stamped sender', () => {
  const { a, b, idA, idB } = pair();

  b.receive({ type: 'offer', to: idA, from: 'spoofed', payload: { ...offer, extra: 'dropped' } });
  assert.deepEqual(a.last(), { type: 'offer', from: idB, payload: offer });

  a.receive({ type: 'answer', to: idB, payload: { type: 'answer', sdp: 'v=0\r\n' } });
  assert.deepEqual(b.last(), { type: 'answer', from: idA, payload: { type: 'answer', sdp: 'v=0\r\n' } });

  const candidate = { candidate: 'candidate:1 1 udp 1 10.0.0.1 5000 typ host', sdpMid: '0', sdpMLineIndex: 0, usernameFragment: 'ufrag', junk: 1 };
  a.receive({ type: 'ice-candidate', to: idB, payload: candidate });
  const { junk, ...clean } = candidate;
  assert.deepEqual(b.last(), { type: 'ice-candidate', from: idA, payload: clean });
});

test('refuses any other message type and relays nothing', () => {
  const { a, b, idB } = pair();
  const before = b.sent.length;
  a.receive({ type: 'chat', to: idB, payload: { text: 'hi' } });
  assert.equal(a.last().code, 'unsupported-type');
  assert.equal(b.sent.length, before);
});

test('rejects malformed payloads, bad JSON, and unknown or self targets', () => {
  const { a, b, idA, idB } = pair();
  const before = b.sent.length;

  a.receive({ type: 'offer', to: idB, payload: { type: 'answer', sdp: 'x' } });
  assert.equal(a.last().code, 'bad-payload');
  a.receive({ type: 'offer', to: idB, payload: { type: 'offer' } });
  assert.equal(a.last().code, 'bad-payload');
  a.receive({ type: 'ice-candidate', to: idB, payload: null });
  assert.equal(a.last().code, 'bad-payload');
  a.receive('{not json');
  assert.equal(a.last().code, 'bad-json');
  a.receive('[1, 2]');
  assert.equal(a.last().code, 'bad-message');
  a.receive({ type: 'offer', to: 'nobody', payload: offer });
  assert.equal(a.last().code, 'unknown-peer');
  a.receive({ type: 'offer', to: idA, payload: offer });
  assert.equal(a.last().code, 'unknown-peer');

  assert.equal(b.sent.length, before);
  assert.equal(a.closed, null);
});

test('closes a socket that sends an oversized frame and tells the other peer', () => {
  const { room, a, b, idA } = pair();
  a.receive({ type: 'offer', to: 'x', payload: { type: 'offer', sdp: 'a'.repeat(LIMITS.MAX_MESSAGE_BYTES) } });
  assert.equal(a.closed.code, 1009);
  assert.deepEqual(b.last(), { type: 'peer-left', peerId: idA });
  assert.equal(room.sessions.size, 1);
});

test('counts multi-byte characters by their UTF-8 size', () => {
  const { a } = pair();
  // Under the limit in UTF-16 code units, over it in UTF-8 bytes.
  a.receive(JSON.stringify({ type: 'ping', pad: '好'.repeat(Math.ceil(LIMITS.MAX_MESSAGE_BYTES / 3) + 1) }));
  assert.equal(a.closed.code, 1009);
});

test('closes a socket that sends binary frames', () => {
  const { a } = pair();
  a.receive(new ArrayBuffer(8));
  assert.equal(a.closed.code, 1003);
});

test('rate limit: a burst is allowed, a flood is closed, a steady trickle is fine', () => {
  const { a, b, clock } = pair();
  for (let i = 0; i < LIMITS.RATE_BURST; i++) a.receive({ type: 'ping' });
  assert.equal(a.closed, null);
  a.receive({ type: 'ping' });
  assert.equal(a.closed.code, 1008);

  const interval = 1000 / LIMITS.RATE_REFILL_PER_SEC;
  for (let i = 0; i < LIMITS.RATE_BURST * 3; i++) {
    clock.now += interval;
    b.receive({ type: 'ping' });
  }
  assert.equal(b.closed, null);
});

test('a third peer is turned away and never joins the map', () => {
  const { room, a, b } = pair();
  const seen = [a.sent.length, b.sent.length];
  const c = new FakeSocket();
  assert.equal(room.join(c), null);
  assert.equal(c.sent[0].code, 'room-full');
  assert.equal(c.closed.code, 4001);
  assert.equal(room.sessions.size, 2);
  assert.deepEqual([a.sent.length, b.sent.length], seen);
});

test('a disconnect frees the slot and notifies the room', () => {
  const { room, a, b, idB } = pair();
  b.hangUp();
  assert.deepEqual(a.last(), { type: 'peer-left', peerId: idB });
  assert.equal(room.sessions.size, 1);
  b.hangUp(); // a late duplicate close is harmless
  assert.equal(a.sent.filter((m) => m.type === 'peer-left').length, 1);
  assert.notEqual(room.join(new FakeSocket()), null);
});

test('idle sweep closes silent sessions, keeps pinging ones, and stops when the room is empty', async () => {
  const { room, clock, storage, a, b, idA } = pair();
  await new Promise(setImmediate); // let join()'s async alarm scheduling settle
  assert.equal(storage.alarm, clock.now + LIMITS.SWEEP_INTERVAL_MS);

  clock.now += LIMITS.IDLE_TIMEOUT_MS - 1000;
  b.receive({ type: 'ping' });
  clock.now += 2000;
  await room.alarm();
  assert.equal(a.closed.code, 4002);
  assert.equal(b.closed, null);
  assert.deepEqual(b.last(), { type: 'peer-left', peerId: idA });
  assert.equal(storage.alarm, clock.now + LIMITS.SWEEP_INTERVAL_MS);

  clock.now += LIMITS.IDLE_TIMEOUT_MS + 1;
  storage.alarm = null;
  await room.alarm();
  assert.equal(b.closed.code, 4002);
  assert.equal(storage.alarm, null);
});

test('worker entry: health, upgrade, origin and room checks before reaching the room', async () => {
  const forwarded = [];
  const env = {
    ALLOWED_ORIGINS: 'https://peer.example.com, http://localhost:8080',
    SIGNALING_ROOMS: {
      idFromName: (name) => `id:${name}`,
      get: (id) => ({ fetch: async (req) => (forwarded.push({ id, url: req.url }), new Response('room')) }),
    },
  };
  const ws = { Upgrade: 'websocket', Origin: 'http://localhost:8080' };
  const call = (path, headers = ws) => worker.fetch(new Request(`https://gw.test${path}`, { headers }), env);

  assert.equal((await call('/health', {})).status, 200);
  assert.equal((await call('/nope')).status, 404);
  assert.equal((await call('/ws?room=demo', {})).status, 426);
  assert.equal((await call('/ws?room=demo', { ...ws, Origin: 'https://evil.test' })).status, 403);
  assert.equal((await call('/ws?room=demo', { Upgrade: 'websocket' })).status, 403);
  assert.equal((await call('/ws?room=bad%20room')).status, 400);
  assert.equal((await call('/ws')).status, 400);
  assert.equal(forwarded.length, 0);

  assert.equal(await (await call('/ws?room=demo')).text(), 'room');
  assert.deepEqual(forwarded, [{ id: 'id:demo', url: 'https://gw.test/ws?room=demo' }]);

  // No allowlist configured: any origin (local development).
  const open = { ...env, ALLOWED_ORIGINS: '' };
  const res = await worker.fetch(new Request('https://gw.test/ws?room=demo', { headers: { Upgrade: 'websocket' } }), open);
  assert.equal(await res.text(), 'room');
});
