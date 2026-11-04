// The browser forwarder (src/mesh/) against the Python reference: golden
// vectors from fixtures/vectors/mesh.json (generate_mesh.py), then the node's
// behaviour over a fake link -- carrying, refusing, flooding, witnessing, caps.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { type CanonObject, canonicalText, fromHex, parseLossless } from '../src/core/canon.ts';
import { WebCryptoSigner } from '../src/core/ed25519.ts';
import { signPayload, verifyPayload } from '../src/core/envelope.ts';
import { readBundle } from '../src/mesh/bundle.ts';
import { BrowserNode, type MeshLink } from '../src/mesh/node.ts';
import {
  DidSet,
  GENESIS,
  INCIDENT,
  type MeshTrust,
  checkAdmission,
  checkDecision,
  checkHeartbeat,
  checkHello,
  checkRequest,
  checkResponse,
  digest,
  encodeFrame,
  lobbyRoom,
  makeHeartbeat,
  makeHello,
  merkleRoot,
  readIncident,
  readReset,
  sdpFingerprint,
} from '../src/mesh/protocol.ts';

const V = JSON.parse(readFileSync(new URL('../../fixtures/vectors/mesh.json', import.meta.url), 'utf8'));
const T0 = 1_790_000_000.5;
const did = (n: string): string => V.identities[n].did;
const obj = (entry: { text: string }): CanonObject => parseLossless(entry.text) as CanonObject;
const signer = (n: string): Promise<WebCryptoSigner> => WebCryptoSigner.fromSeed(fromHex(V.identities[n].seed));

function trust(over: Partial<Record<keyof MeshTrust, string[]>> = {}): MeshTrust {
  const lists = {
    mesh: [did('server'), did('provider'), did('broker'), did('browser')],
    users: [did('user')],
    deciders: [did('server')],
    coordinators: [],
    brokers: [did('broker')],
    operators: [did('operator')],
    ...over,
  };
  return Object.fromEntries(Object.entries(lists).map(([k, v]) => [k, new DidSet(v)])) as unknown as MeshTrust;
}

// -- vectors -----------------------------------------------------------------------------------

test('every Python-signed mesh object re-encodes to its bytes, digest and signature', async () => {
  for (const [n, id] of Object.entries(V.identities) as [string, { did: string }][]) {
    assert.equal((await signer(n)).did, id.did, n);
  }
  const entries = [
    ...Object.values(V.heartbeats), V.request, V.decision, V.lease, V.response, V.admission, V.hello, V.incident, V.reset,
  ] as { text: string; digest: string }[];
  for (const e of entries) {
    const o = obj(e);
    assert.equal(canonicalText(o), e.text);
    assert.equal(await digest(o), e.digest);
    assert.equal((await verifyPayload(o)).ok, true);
  }
});

test('the browser signs heartbeats and hellos byte-identical to Python', async () => {
  const b = await signer('browser');
  const hb = V.heartbeats;
  const first = await makeHeartbeat(b, {
    seq: BigInt(hb.browser_first.seq), prev: GENESIS, issuedAt: Number(hb.browser_first.issued_at),
    neighbors: new Map([[did('server'), { seq: 0n, head: '' }]]),
  });
  assert.equal(canonicalText(first), hb.browser_first.text);
  const second = await makeHeartbeat(b, {
    seq: BigInt(hb.browser_second.seq), prev: await digest(first), issuedAt: Number(hb.browser_second.issued_at),
    neighbors: new Map([
      [did('server'), { seq: (obj(hb.server)['seq'] as bigint), head: hb.server.digest.slice(0, 32) }],
      [did('provider'), { seq: 0n, head: '' }],
    ]),
  });
  assert.equal(canonicalText(second), hb.browser_second.text);
  const hello = await makeHello(b, {
    room: V.hello.room, localFp: V.hello.local_fp, remoteFp: V.hello.remote_fp, issuedAt: T0, admission: obj(V.admission),
  });
  assert.equal(canonicalText(hello), V.hello.text);
});

test('the checks accept what Python made and the derived values agree', async () => {
  const t = trust();
  const request = obj(V.request);
  const decision = obj(V.decision);
  assert.equal(await checkHeartbeat(obj(V.heartbeats.server), t, T0), null);
  assert.equal(await checkHeartbeat(obj(V.heartbeats.browser_second), t, T0), null);
  assert.equal(await checkRequest(request, t, T0 + 1), null);
  assert.equal(await checkRequest(request, t, T0 + 61), 'expired');
  assert.equal(await checkDecision(decision, t, request), null);
  assert.equal(await checkDecision(decision, trust({ deciders: [] }), request), 'signer not trusted');
  assert.equal(await checkResponse(obj(V.response), t, decision), null);
  assert.equal(await checkAdmission(obj(V.admission), t, T0), null);
  assert.equal(await checkAdmission(obj(V.admission), t, T0 + 86400), 'expired');
  const inc = await readIncident(obj(V.incident), t);
  assert.deepEqual([...inc!.targets], [`decider:${did('server')}`]);
  assert.deepEqual(await readReset(obj(V.reset), t), [[`decider:${did('server')}`], [V.incident.incident_id]]);
  for (const r of V.lobby_rooms) assert.equal(await lobbyRoom(r.did, r.salt), r.room);
  for (const m of V.merkle) assert.equal(await merkleRoot(m.leaves), m.root);
  // the hello as the server sees it: the browser's DID bound to both fingerprints
  const now = T0;
  const ok = await checkHello(obj(V.hello), t, { room: V.hello.room, myFp: V.hello.remote_fp, peerFp: V.hello.local_fp, now });
  assert.equal(ok, did('browser'));
  await assert.rejects(
    checkHello(obj(V.hello), t, { room: V.hello.room, myFp: V.hello.local_fp, peerFp: V.hello.remote_fp, now }),
    /fingerprints/,
  );
  await assert.rejects(checkHello(obj(V.hello), trust({ mesh: [did('server')] }), {
    room: V.hello.room, myFp: V.hello.remote_fp, peerFp: V.hello.local_fp, now,
  }), /not trusted/);
  const frame = parseLossless(V.frames.req) as CanonObject;
  assert.equal(canonicalText(frame['decision']!), V.decision.text);
  assert.equal(sdpFingerprint('v=0\r\na=fingerprint:SHA-256 ab:cd\r\n'), 'sha-256 AB:CD');
});

// -- the node --------------------------------------------------------------------------------

class Wire implements MeshLink {
  sent: { to: string; frame: CanonObject }[] = [];
  down = new Set<string>();
  send(to: string, text: string): boolean {
    if (this.down.has(to)) return false;
    this.sent.push({ to, frame: parseLossless(text) as CanonObject });
    return true;
  }
  take(): { to: string; frame: CanonObject }[] {
    return this.sent.splice(0);
  }
}

async function node(clock = (): number => T0 + 1, config = {}) {
  const b = new BrowserNode(await signer('browser'), trust({ mesh: [did('server'), did('provider'), did('broker')] }), {
    clock, config,
  });
  const wire = new Wire();
  b.attach(wire);
  assert.equal(await b.publish(obj(V.admission)), true); // its own admission, as the page does
  wire.take();
  return { b, wire };
}

const reqFrame = (hop = 1): string =>
  encodeFrame('req', { request: obj(V.request), decision: obj(V.decision), leg: 0, hop });

test('a req is carried one hop toward the model node, only from the hop before', async () => {
  const { b, wire } = await node();
  assert.equal(b.bind(did('server')) && b.bind(did('provider')), true);
  wire.take(); // the dir? queries
  await b.handle(did('provider'), reqFrame()); // from the wrong side
  await b.handle(did('server'), reqFrame(2));  // the wrong position
  assert.deepEqual(wire.take(), []);
  await b.handle(did('server'), reqFrame());
  const [out] = wire.take();
  assert.equal(out!.to, did('provider'));
  assert.equal(out!.frame['k'], 'req');
  assert.equal(out!.frame['hop'], 2);
  assert.equal(canonicalText(out!.frame['request']!), V.request.text);
  assert.equal(b.stats().carried, 1);
});

test('a resp walks back toward the decider and a dead next hop turns the leg back', async () => {
  const { b, wire } = await node();
  b.bind(did('server'));
  b.bind(did('provider'));
  wire.take();
  await b.handle(did('provider'), encodeFrame('resp', { decision: obj(V.decision), response: obj(V.response), leg: 0, hop: 1 }));
  const [back] = wire.take();
  assert.equal(back!.to, did('server'));
  assert.equal(back!.frame['hop'], 0);
  assert.equal(canonicalText(back!.frame['response']!), V.response.text);

  wire.down.add(did('provider'));
  await b.handle(did('server'), reqFrame());
  const [nack] = wire.take();
  assert.equal(nack!.to, did('server'));
  assert.equal(nack!.frame['k'], 'nack');
  assert.equal(nack!.frame['hop'], 0);
  assert.equal(nack!.frame['at'], did('browser'));
  assert.equal(nack!.frame['unreachable'], did('provider'));
  assert.equal(nack!.frame['reason'], 'the next hop is unreachable');
});

test('in Lockdown only Lockdown decisions pass, until an operator reset', async () => {
  const { b, wire } = await node();
  b.bind(did('server'));
  b.bind(did('provider'));
  wire.take();
  // a mutiny incident against the model (self-reported by the provider): Lockdown, decider not latched
  const provider = await signer('provider');
  const evidence = { reporter: did('provider'), lease: obj(V.lease), violation: 'asked to exfiltrate' };
  const kind = 'model.self-reported';
  const incident = await signPayload(provider, {
    type: INCIDENT, reporter: did('provider'), kind, evidence, incident_id: await digest({ kind, evidence }),
    issued_at: obj(V.request)['issued_at']!,
  });
  await b.handle(did('provider'), encodeFrame('flood', { obj: incident }));
  assert.equal(b.mode, 'lockdown');
  assert.deepEqual(wire.take().map((s) => s.to), [did('server')]); // flooded on, not back
  await b.handle(did('server'), reqFrame());
  const [nack] = wire.take();
  assert.equal(nack!.frame['k'], 'nack');
  assert.equal(nack!.frame['reason'], 'lockdown: the decision layer is suspended');

  // the decider's own mutiny latches it: refused for that reason first
  await b.handle(did('server'), encodeFrame('flood', { obj: obj(V.incident) }));
  wire.take();
  assert.equal(b.mayCarry(obj(V.decision)), 'the decider is latched');
  await b.handle(did('provider'), encodeFrame('flood', { obj: obj(V.reset) }));
  assert.equal(b.mayCarry(obj(V.decision)), 'lockdown: the decision layer is suspended'); // the model latch holds
  assert.equal(b.ledger.excludesNode(did('server')), false);
});

test('floods: verified and new objects travel on once; forgeries and strangers stop here', async () => {
  const { b, wire } = await node();
  b.bind(did('server'));
  b.bind(did('provider'));
  wire.take();
  const hb = obj(V.heartbeats.server);
  await b.handle(did('server'), encodeFrame('flood', { obj: hb }));
  assert.deepEqual(wire.take().map((s) => s.to), [did('provider')]);
  await b.handle(did('provider'), encodeFrame('flood', { obj: hb })); // again, another way
  assert.deepEqual(wire.take(), []);
  const forged = { ...obj(V.heartbeats.server), load: obj(V.heartbeats.browser_first)['load']! };
  await b.handle(did('server'), encodeFrame('flood', { obj: forged }));
  const stranger = await WebCryptoSigner.generate();
  const theirs = await makeHeartbeat(stranger, { seq: 5n, prev: GENESIS, neighbors: new Map(), issuedAt: T0 });
  await b.handle(did('server'), encodeFrame('flood', { obj: theirs }));
  assert.deepEqual(wire.take(), []);
  // an unbound peer is not heard at all
  await b.handle(did('broker'), encodeFrame('flood', { obj: obj(V.reset) }));
  assert.deepEqual(wire.take(), []);
});

test("the node's heartbeat witnesses what a neighbour handed it directly", async () => {
  const { b, wire } = await node();
  b.bind(did('server'));
  b.bind(did('provider'));
  wire.take();
  await b.handle(did('server'), encodeFrame('flood', { obj: obj(V.heartbeats.server) }));
  wire.take();
  await b.tick();
  const sent = wire.take();
  assert.deepEqual(sent.map((s) => s.to), [did('provider'), did('server')]);
  const mine = sent[0]!.frame['obj'] as CanonObject;
  assert.equal(await checkHeartbeat(mine, trust(), T0 + 1), null);
  assert.deepEqual(mine['roles'], ['forward']);
  assert.equal(mine['ledger_root'], GENESIS);
  const witness = mine['neighbors'] as unknown as [string, bigint | number, string][];
  const server = witness.find((w) => w[0] === did('server'))!;
  assert.equal(BigInt(server[1]), obj(V.heartbeats.server)['seq']);
  assert.equal(server[2], V.heartbeats.server.digest.slice(0, 32));
  assert.deepEqual(witness.find((w) => w[0] === did('provider')), [did('provider'), 0, '']);
});

test('caps: frame size, rate per neighbour, neighbour count; the directory answers', async () => {
  let now = T0 + 1;
  const { b, wire } = await node(() => now, { maxFrameChars: 2000, frameBurst: 3, frameRate: 1, maxNeighbors: 2 });
  assert.equal(b.bind(did('server')) && b.bind(did('provider')), true);
  assert.equal(b.bind(did('broker')), false);
  wire.take();
  await b.handle(did('server'), 'x'.repeat(2001));
  for (let i = 0; i < 5; i++) await b.handle(did('server'), encodeFrame('dir?'));
  assert.equal(b.stats().dropped, 3); // one too big, two over the burst
  const dirs = wire.take();
  assert.equal(dirs.length, 3);
  assert.equal(dirs[0]!.frame['k'], 'dir');
  assert.equal((dirs[0]!.frame['admissions'] as CanonObject[]).length, 1);
  now += 2; // two tokens back
  await b.handle(did('server'), encodeFrame('dir?'));
  assert.equal(wire.take().length, 1);
});

test('a join bundle must carry an admission for this browser that verifies', async () => {
  const t = { mesh: [did('server')], users: [], deciders: [did('server')], coordinators: [], brokers: [], operators: [did('operator')] };
  const bundle = (over: object = {}) => canonicalText({
    type: 'secdogie/inference/join/v1', signaling: 'ws://127.0.0.1:1/ws', entries: [did('server')], trust: t,
    admission: obj(V.admission), ...over,
  } as CanonObject);
  const ok = await readBundle(bundle(), did('browser'), T0);
  assert.deepEqual(ok.entries, [did('server')]);
  assert.equal(ok.heartbeatEvery, 2);
  await assert.rejects(readBundle(bundle(), did('server'), T0), /not for this browser/);
  await assert.rejects(readBundle(bundle(), did('browser'), T0 + 86400), /expired/);
  await assert.rejects(readBundle(bundle({ entries: [did('provider')] }), did('browser'), T0), /not on the mesh list/);
  await assert.rejects(readBundle(bundle({ trust: { ...t, operators: [] } }), did('browser'), T0), /not trusted/);
});
