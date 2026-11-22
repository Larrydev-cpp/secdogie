import test from 'node:test';
import assert from 'node:assert/strict';

import {
  IdempotentReceiver,
  LinkTier,
  SequencedFrameQueue,
  StarRole,
} from '../client/mesh.js';

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
