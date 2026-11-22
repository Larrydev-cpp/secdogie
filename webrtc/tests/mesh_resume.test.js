import test from 'node:test';
import assert from 'node:assert/strict';

import {
  IdempotentReceiver,
  LinkTier,
  SequencedFrameQueue,
  StarRole,
} from '../client/mesh.js';
import { canonical } from '../client/pairing.js';
import {
  DEFAULT_ICE_SERVERS,
  parseIceServers,
} from '../client/web_peer.js';

test('SequencedFrameQueue assigns monotonic seq_id, trims on ack, and replays on reconnect', () => {
  const q = new SequencedFrameQueue(4);
  const f1 = q.enqueue({ m: 'control', op: 'approve', id: 'm1' });
  const f2 = q.enqueue({ m: 'control', op: 'approve', id: 'm2' });
  const f3 = q.enqueue({ m: 'control', op: 'approve', id: 'm3' });

  assert.equal(f1.seq_id, 1);
  assert.equal(f2.seq_id, 2);
  assert.equal(f3.seq_id, 3);
  assert.equal(q.pendingCount, 3);

  assert.equal(q.ack(1), 1);
  assert.equal(q.pendingCount, 2);
  assert.deepEqual(q.resumeMessage(1), { m: 'resume', last_ack_seq: 1 });

  const replay = q.replayAfter(1);
  assert.deepEqual(replay.map((f) => f.seq_id), [2, 3]);
  assert.equal(q.ack(3), 2);
  assert.equal(q.pendingCount, 0);
});

test('IdempotentReceiver prevents duplicate execution on replayed seq_id frames', () => {
  const rx = new IdempotentReceiver(64);
  assert.deepEqual(rx.accept(1), { isNew: true, ackSeq: 1 });
  assert.deepEqual(rx.accept(1), { isNew: false, ackSeq: 1 });
  assert.deepEqual(rx.accept(2), { isNew: true, ackSeq: 2 });
  assert.deepEqual(rx.accept(1), { isNew: false, ackSeq: 2 });
  assert.equal(rx.lastDeliveredSeq, 2);
});

test('LinkTier and StarRole constants expose V1.2 star and 3-tier names', () => {
  assert.equal(LinkTier.LAN, 'lan');
  assert.equal(LinkTier.WEBRTC, 'webrtc');
  assert.equal(LinkTier.RELAY, 'relay');
  assert.equal(StarRole.HUB, 'hub');
  assert.equal(StarRole.SPOKE, 'spoke');
});

test('DEFAULT_ICE_SERVERS includes multi-region STUN fallbacks and parseIceServers supports TURN', () => {
  const urls = DEFAULT_ICE_SERVERS.map((s) => s.urls);
  assert.ok(urls.some((u) => u.includes('cloudflare.com')));
  assert.ok(urls.some((u) => u.includes('miwifi.com') || u.includes('qq.com')));
  assert.ok(urls.some((u) => u.includes('google.com')));

  const turnJson = JSON.stringify([{ urls: 'turn:relay.example.com:3478', username: 'u', credential: 'p' }]);
  const parsedJson = parseIceServers(turnJson);
  assert.equal(parsedJson.length, 1);
  assert.equal(parsedJson[0].urls, 'turn:relay.example.com:3478');
  assert.equal(parsedJson[0].username, 'u');

  const parsedCsv = parseIceServers('stun:custom.example.com:3478');
  assert.equal(parsedCsv[0].urls, 'stun:custom.example.com:3478');
  assert.ok(parsedCsv.length > 1);
});

test('canonical JSON rejects lone surrogates even when String.prototype.isWellFormed is absent', () => {
  const orig = String.prototype.isWellFormed;
  try {
    // Simulate older iOS Safari (< 16.4) / older Android WebView
    Object.defineProperty(String.prototype, 'isWellFormed', { value: undefined, configurable: true, writable: true });
    assert.equal(canonical('你好😀'), '"你好😀"');
    assert.throws(() => canonical('\ud800'), TypeError);
    assert.throws(() => canonical('\udfff'), TypeError);
  } finally {
    Object.defineProperty(String.prototype, 'isWellFormed', { value: orig, configurable: true, writable: true });
  }
});
