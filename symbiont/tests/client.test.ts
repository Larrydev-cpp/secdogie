// The client across a dropped link: open cards wait ("when you are back")
// instead of failing, and come back -- same question, same consent, freshly
// armed -- when the node shows them again after HELLO. Approve/Deny answers
// need a real user activation.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { Turn } from '../src/client/core_link.ts';
import { type FrameLink, OperatorClient } from '../src/client/operator_client.ts';
import { type Signer, WebCryptoSigner } from '../src/core/ed25519.ts';
import { DemoAgent } from '../src/demo/agent.ts';
import { MemoryKeyring } from '../src/gate2/signer_flow.ts';
import type { LinkPhase, PhaseInfo } from '../src/net/attach.ts';
import type { Keys } from '../src/net/keystore.ts';
import { zh } from '../src/voice/locale.ts';

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
async function until(pred: () => boolean, what: string): Promise<void> {
  const end = Date.now() + 3000;
  while (!pred()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await wait(5);
  }
}

class TestLink implements FrameLink {
  phase: LinkPhase = 'connected';
  info: PhaseInfo = { reason: null };
  readonly pairing = null;
  readonly canApprove = true;
  readonly keys: Keys;
  readonly nodeDid: string;
  readonly agent: DemoAgent;
  up = true;
  readonly #phase = new Set<(p: LinkPhase, i: PhaseInfo) => void>();
  readonly #frames = new Set<(d: Uint8Array<ArrayBuffer>) => void>();
  readonly #bound = new Set<(id: number) => void>();

  constructor(keys: Keys, node: WebCryptoSigner) {
    this.keys = keys;
    this.nodeDid = node.did;
    this.agent = new DemoAgent({
      node, appDid: keys.app.did, operatorDid: keys.operator.did, lang: 'zh', delay: () => wait(1),
      send: (raw) => queueMicrotask(() => {
        if (this.up) for (const cb of this.#frames) cb(raw);
      }),
    });
  }
  onPhase(cb: (p: LinkPhase, i: PhaseInfo) => void) {
    this.#phase.add(cb);
    return () => this.#phase.delete(cb);
  }
  onFrame(cb: (d: Uint8Array<ArrayBuffer>) => void) {
    this.#frames.add(cb);
    return () => this.#frames.delete(cb);
  }
  onBound(cb: (id: number) => void) {
    this.#bound.add(cb);
    return () => this.#bound.delete(cb);
  }
  async send(d: Uint8Array) {
    if (this.up) await this.agent.receive(d);
    return this.up;
  }
  async confirmPairing() {
    return false;
  }
  cancelPairing() {}
  set(phase: LinkPhase) {
    this.phase = phase;
    this.up = phase === 'connected';
    for (const cb of this.#phase) cb(phase, this.info);
  }
  bind() {
    for (const cb of this.#bound) cb(1);
  }
}

const timers = {
  set: (fn: () => void, ms: number) => setTimeout(fn, ms),
  clear: (h: unknown) => clearTimeout(h as ReturnType<typeof setTimeout>),
  every: (fn: () => void, ms: number) => setInterval(fn, ms),
  cancel: (h: unknown) => clearInterval(h as ReturnType<typeof setInterval>),
};

async function world(activation?: () => boolean) {
  const keys = { app: await WebCryptoSigner.generate(), operator: await WebCryptoSigner.generate() };
  const link = new TestLink(keys, await WebCryptoSigner.generate());
  const client = new OperatorClient({
    link, voice: zh, timers, keyringFor: (op: Signer) => new MemoryKeyring(op), armingDelay: 0.05,
    ...(activation ? { activation } : {}),
  });
  link.agent.start();
  link.bind();
  return { link, client, stop: () => { client.dispose(); link.agent.stop(); } };
}

const find = <K extends Turn['kind']>(c: OperatorClient, kind: K) =>
  [...c.turns()].reverse().find((t): t is Extract<Turn, { kind: K }> => t.kind === kind);

test('a dropped link: the consent waits, then comes back when the node shows it again', async () => {
  const { link, client, stop } = await world();
  try {
    client.say('把旧账号注销掉');
    await until(() => find(client, 'ask') !== undefined, 'the question');
    client.answer(find(client, 'ask')!.id, find(client, 'ask')!.options[0]!.value);
    await until(() => find(client, 'consent') !== undefined, 'the consent');
    const id = find(client, 'consent')!.id;
    link.set('reconnecting');
    assert.equal(find(client, 'consent')!.state, 'waiting');
    assert.equal(client.presence.phase, 'reconnecting');
    await wait(50);
    link.set('connected');
    link.bind(); // HELLO: the node shows what is still open
    await until(() => find(client, 'consent')!.state === 'awaiting', 'the consent back');
    assert.equal(find(client, 'consent')!.id, id, 'the same card, not a second one');
    assert.equal(client.turns().filter((t) => t.kind === 'consent').length, 1);
    await wait(80);
    await client.approve(id);
    await until(() => find(client, 'consent')!.state === 'done', 'done');
  } finally {
    stop();
  }
});

test('words typed while the link is down are not pretended sent', async () => {
  const { link, client, stop } = await world();
  try {
    link.set('unreachable');
    client.say('整理桌面');
    const last = client.turns().at(-1)!;
    assert.equal(last.kind, 'say');
    assert.equal((last as Extract<Turn, { kind: 'say' }>).text, zh.reply.undelivered);
  } finally {
    stop();
  }
});

test('an Approve answer outside a real user activation is not sent', async () => {
  let active = false;
  const { client, stop } = await world(() => active);
  try {
    client.say('每1秒检查一次邮箱');
    await until(() => find(client, 'ask') !== undefined, 'the question');
    const ask = find(client, 'ask')!;
    // the demo offers plain options; pretend this one is the loop's Approve/Deny
    const fake = { ...ask, options: [{ label: '可以', value: 'Approve' }] };
    client.conv.update(ask.id, fake);
    client.answer(ask.id, 'Approve');
    assert.equal(find(client, 'ask')!.state, 'open');
    active = true;
    client.answer(ask.id, 'Approve');
    assert.equal(find(client, 'ask')!.state, 'answered');
  } finally {
    stop();
  }
});
