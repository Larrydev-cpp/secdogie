// The whole loop on the demo core -- the page's real client talking signed
// dialogue/v1 packets to the in-page node:
//   FlowState (you are typing: the question waits) -> AttentionGap (it appears)
//   -> Gate 1 (two similar places: which one?) -> Gate 2 (an irreversible step:
//   armed after a moment, signed on your tap, verified by the node) -> done.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { Signer } from '../src/core/ed25519.ts';
import type { Turn } from '../src/client/core_link.ts';
import { demoCore } from '../src/demo/core.ts';
import { MemoryKeyring } from '../src/gate2/signer_flow.ts';
import { en, zh } from '../src/voice/locale.ts';

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

async function until(pred: () => boolean, what: string, ms = 3000): Promise<void> {
  const end = Date.now() + ms;
  while (!pred()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await wait(5);
  }
}

const timers = {
  set: (fn: () => void, ms: number) => setTimeout(fn, ms),
  clear: (h: unknown) => clearTimeout(h as ReturnType<typeof setTimeout>),
  every: (fn: () => void, ms: number) => setInterval(fn, ms),
  cancel: (h: unknown) => clearInterval(h as ReturnType<typeof setInterval>),
};

async function start(voice = zh) {
  const { client, link } = await demoCore(voice, {
    keyringFor: (op: Signer) => new MemoryKeyring(op),
    delay: () => wait(1),
    timers,
  });
  return { client, link, stop: () => { client.dispose(); link.agent.stop(); } };
}

const last = <K extends Turn['kind']>(turns: readonly Turn[], kind: K) =>
  [...turns].reverse().find((t): t is Extract<Turn, { kind: K }> => t.kind === kind);

test('FlowState -> AttentionGap -> Gate 1 -> Gate 2 -> done', async () => {
  const { client, stop } = await start();
  try {
    assert.equal(client.presence.phase, 'demo');
    // FlowState: you are still typing -- the question the node raises waits
    client.hold(true);
    client.say('把旧账号注销掉');
    await until(() => client.turns().some((t) => t.kind === 'say' && t.text === zh.reply.accepted), 'the node accepting');
    await wait(50);
    assert.equal(last(client.turns(), 'ask'), undefined, 'no card while you type');
    // AttentionGap: you pause -- it appears
    client.hold(false);
    await until(() => last(client.turns(), 'ask') !== undefined, 'the Gate 1 card');
    const ask = last(client.turns(), 'ask')!;
    assert.equal(ask.lead, '刚才找到了两个相似的地方，帮你确认一下是这个吗？');
    assert.equal(ask.options.length, 2);
    assert.equal(client.replyTo, ask.id);
    // Gate 1: you pick one
    client.answer(ask.id, ask.options[0]!.value);
    await until(() => last(client.turns(), 'consent') !== undefined, 'the Gate 2 card');
    const consent = last(client.turns(), 'consent')!;
    assert.equal(consent.sentence, '这一步会直接注销旧账号（在 docs.example.com），做完就没法恢复了。确认要继续吗？');
    assert.equal(consent.grave, true);
    assert.ok(consent.canApprove);
    // Gate 2: too soon does nothing; after the arming delay, your tap signs
    await client.approve(consent.id);
    assert.equal((client.turns().find((t) => t.id === consent.id) as typeof consent).state, 'awaiting');
    await wait(850);
    await client.approve(consent.id);
    await until(() => client.turns().some((t) => t.kind === 'say' && t.text === zh.reply.done), 'done');
    assert.equal((client.turns().find((t) => t.id === consent.id) as typeof consent).state, 'done');
    assert.equal(client.activity.running, false);
  } finally {
    stop();
  }
});

test('cancelling the irreversible step: it does not happen', async () => {
  const { client, stop } = await start(en);
  try {
    client.say('close the old account');
    await until(() => last(client.turns(), 'ask') !== undefined, 'the question');
    const ask = last(client.turns(), 'ask')!;
    assert.equal(ask.lead, 'I found two similar places. Is it this one?');
    client.answer(ask.id, ask.options[1]!.value);
    await until(() => last(client.turns(), 'consent') !== undefined, 'the consent');
    const c = last(client.turns(), 'consent')!;
    assert.equal(c.sentence, 'This step will close the old account (on forum.example.com), and it cannot be undone. Are you sure you want to proceed?');
    client.cancel(c.id);
    await until(() => client.turns().some((t) => t.kind === 'say' && t.text === en.reply.notDone), 'not done');
    assert.equal((client.turns().find((t) => t.id === c.id) as typeof c).state, 'cancelled');
  } finally {
    stop();
  }
});

test('a vague wording gets a Socratic question first; a plain goal just gets done', async () => {
  const { client, stop } = await start();
  try {
    client.say('每1秒检查一次邮箱');
    await until(() => last(client.turns(), 'ask') !== undefined, 'the wording question');
    const ask = last(client.turns(), 'ask')!;
    assert.equal(ask.lead, zh.ask.finding.polling);
    client.answer(ask.id, ask.options[0]!.value);
    await until(() => client.turns().filter((t) => t.kind === 'say' && t.text === zh.reply.done).length === 1, 'done');
    client.say('整理一下桌面上的截图');
    await until(() => client.turns().filter((t) => t.kind === 'say' && t.text === zh.reply.done).length === 2, 'done again');
  } finally {
    stop();
  }
});

test('stop: the running goal ends, whoever started it', async () => {
  const { client, stop } = await start();
  try {
    client.say('把旧账号注销掉');
    await until(() => client.activity.running, 'running');
    client.stop();
    await until(() => client.turns().some((t) => t.kind === 'say' && t.text === zh.reply.stopped), 'stopped');
    await until(() => !client.activity.running, 'idle');
  } finally {
    stop();
  }
});
