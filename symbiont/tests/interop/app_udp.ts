// The page's own wire stack -- direct/v1 frames, the mux, the dialogue session,
// dialogue/v1 envelopes, Gate 2 -- talking to a real Python node over UDP.
// Driven by node/tests/test_symbiont_interop.py: config on stdin, one JSON line
// per event on stdout, exit 0 when the goal finished.
//
//   {"node_host", "node_port", "node_did", "app_seed_hex", "operator_seed_hex", "answer"}
import { createSocket } from 'node:dgram';

import { fromHex } from '../../src/core/canon.ts';
import { WebCryptoSigner } from '../../src/core/ed25519.ts';
import { TrustSet } from '../../src/core/envelope.ts';
import { respond } from '../../src/gate2/guard.ts';
import { ControlOp, DialogueType, PacketKind, SessionEvent, Verdict, dialoguePacket } from '../../src/gate2/wire.ts';
import { DirectFrames } from '../../src/net/frame.ts';
import { DIALOGUE_CHANNEL, muxDecode, muxEncode } from '../../src/net/mux.ts';
import { DialogueSession } from '../../src/net/session.ts';

const chunks: Buffer[] = [];
for await (const c of process.stdin) chunks.push(c as Buffer);
const cfg = JSON.parse(Buffer.concat(chunks).toString('utf8'));

const app = await WebCryptoSigner.fromSeed(fromHex(cfg.app_seed_hex));
const operator = await WebCryptoSigner.fromSeed(fromHex(cfg.operator_seed_hex));
const trust = new TrustSet([cfg.node_did]);
const frames = new DirectFrames({ self: app, peerDid: cfg.node_did, trust });
const sock = createSocket('udp4');
const out = (o: unknown) => process.stdout.write(JSON.stringify(o) + '\n');

const session = new DialogueSession({
  self: app,
  peerDid: cfg.node_did,
  trust,
  send: (f) => {
    void frames.build(muxEncode(DIALOGUE_CHANNEL, f)).then((raw) => sock.send(raw, cfg.node_port, cfg.node_host));
  },
});

const timer = setTimeout(() => {
  out({ event: 'timeout' });
  process.exit(2);
}, 60_000);

session.onEnvelope = async ({ packet }) => {
  if (packet.kind === PacketKind.Dialogue) {
    const d = packet.packet;
    if (d.dialogue_type === DialogueType.SystemStatus) {
      out({ event: 'status', content: d.content, about: d.in_reply_to });
      if (d.content === 'status: idle' && !started) {
        started = true;
        await session.send({ kind: PacketKind.Control, packet: { request_id: 'r-1', op: ControlOp.AddGoal, goal_id: 'g-1', title: '清理旧文件 — tidy old files', memory_id: '', confirmation: {} } });
      }
      if (d.content.startsWith('goal g-1 finished')) {
        clearTimeout(timer);
        out({ event: 'done', content: d.content });
        setTimeout(() => process.exit(d.content.includes('exit 0') ? 0 : 1), 300);
      }
    } else if (d.dialogue_type === DialogueType.SocraticQuestion) {
      out({ event: 'question', options: d.suggested_options });
      const answer = d.suggested_options.includes('Approve') ? 'Approve' : cfg.answer;
      await session.send({
        kind: PacketKind.Dialogue,
        packet: dialoguePacket({ probe_id: `a-${d.probe_id}`, dialogue_type: DialogueType.UserClarification, content: answer, in_reply_to: d.probe_id }),
      });
    }
  } else if (packet.kind === PacketKind.Gate2Challenge) {
    out({ event: 'challenge', kind: packet.packet.target_action.kind, high_risk: packet.packet.target_action.high_risk });
    const r = await respond(packet.packet, Verdict.Approve, { peerDid: cfg.node_did, operator, now: Date.now() / 1000 });
    await session.send({ kind: PacketKind.Gate2Response, packet: r });
  } else if (packet.kind === PacketKind.StateSnapshot) {
    out({ event: 'snapshot-dropped' });
  }
};
let started = false;

sock.on('message', async (msg) => {
  const data = await frames.open(new Uint8Array(msg));
  if (data === null) return;
  const m = muxDecode(data);
  if (m?.channel === DIALOGUE_CHANNEL) await session.receive(m.payload);
});
sock.bind(0, '127.0.0.1', () => {
  session.start(50);
  void session.send({ kind: PacketKind.Session, packet: { event: SessionEvent.Hello, note: '' } });
  out({ event: 'hello-sent', app: app.did });
});
