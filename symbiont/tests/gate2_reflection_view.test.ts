// Gate 2's reflection in the page's words: every recalled line says where it
// came from, the questions are asked before the last step, the gentler paths
// come first, "I've thought it through" only reveals the confirm card, it says
// so when it knows nothing, and nothing in it reassures or hurries.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { type CanonObject, canonicalText } from '../src/core/canon.ts';
import { type Lang, choose, reflectionView } from '../src/gate2/reflection_view.ts';
import * as pk from '../src/net/packets.ts';

const V = JSON.parse(readFileSync(new URL('../../fixtures/vectors/symbiont_link.json', import.meta.url), 'utf8'));
const enc = new TextEncoder();
const goodNamed = (name: string): CanonObject => {
  const c = V.packets.good.find((g: { name: string }) => g.name === name);
  return pk.decode(enc.encode(c.text));
};
const typed = (p: CanonObject) => pk.asNodePacket(p) as pk.Gate2Challenge;
const with_ = (base: CanonObject, reflection: CanonObject) => typed(pk.validate({ ...base, reflection }));

const RICH = goodNamed('challenge');
const BARE = goodNamed('challenge with a 160-code-point memory');
const NOW = 1759740060000; // a minute after the "why now" goal, same day in UTC
const UTC = { timeZone: 'UTC', nowMs: NOW };

test('it recalls what the account carried, each line with where it came from', () => {
  const v = reflectionView(typed(RICH), { lang: 'zh', ...UTC });
  assert.equal(v.headline, '它陪了你 9 年。你曾让它：『用这个号给阿杰发生日祝福』');
  assert.equal(v.knowsNothing, false);
  assert.match(v.recall[0]!.text, /『用这个号给阿杰发生日祝福』$/);
  assert.equal(v.recall[0]!.source, '你的原话');
  assert.deepEqual(v.recall[1], { text: '旧号里有大学四年的相册，没别处备份', source: '你确认过的' });
  assert.match(v.recall[2]!.text, /办过 2 件事/);
  assert.match(v.recall[3]!.text, /开始用它，到现在 \d+ 年了$/);
  assert.match(v.recall[3]!.source, /^核心 .* 看到的$/);
  assert.equal(v.recall[4]!.text, '里面有 312 条内容');
  for (const line of v.recall) assert.ok(line.source.length > 0, 'every memory says where it came from');
  assert.match(v.whyNow!, /^今天 08:39，你说：『帮我把那个旧账号注销掉』$/);
});

test('it asks before the last step, and offers the gentler paths first', () => {
  const v = reflectionView(typed(RICH), { lang: 'zh', ...UTC });
  assert.deepEqual(v.questions, [
    '你想注销它，是想放下什么？',
    '这些年它装下的东西，你想留一份吗？',
    '这一步做完就找不回来了。一个月后的你，会怎么看今天？',
  ]);
  assert.deepEqual(v.choices.map((c) => c.id), ['export', 'deactivate', 'snooze', 'proceed']);
  assert.equal(v.choices.at(-1)!.label, '我想清楚了');
  assert.deepEqual(v.final, { title: '最后一步', think: '再想想', go: '确认放行' });
});

test('recency is asked about in relative days, in the viewer’s zone', () => {
  const r = { recall: [{ kind: 'saw', fact: 'last_active_at', value: NOW - 3 * 86_400_000, at_ms: NOW }],
              questions: ['motive', 'recent', 'later'], paths: ['snooze'] };
  const zh = reflectionView(with_(RICH, r), { lang: 'zh', ...UTC });
  assert.equal(zh.questions[1], '你 3 天前还在用它。是什么让你今天决定放下？');
  assert.equal(zh.recall[0]!.text, '你最近一次用它是 3 天前');
  const en = reflectionView(with_(RICH, r), { lang: 'en', ...UTC });
  assert.equal(en.questions[1], 'You were still using it 3 days ago. What made today the day?');
});

test('when it knows nothing it says so, and still asks', () => {
  const v = reflectionView(typed(BARE), { lang: 'zh', ...UTC });
  assert.equal(v.knowsNothing, true);
  assert.deepEqual(v.recall, []);
  assert.equal(v.whyNow, null);
  assert.deepEqual(v.questions, ['你想让它消失，是想放下什么？', '这一步做完就找不回来了。一个月后的你，会怎么看今天？']);
  assert.deepEqual(v.choices.map((c) => c.id), ['snooze', 'proceed']);
});

test('English wording, with plurals', () => {
  const v = reflectionView(typed(RICH), { lang: 'en', ...UTC });
  assert.equal(v.recall[0]!.source, 'your words');
  assert.match(v.recall[2]!.text, /^It did 2 things with it for you/);
  assert.equal(v.recall[4]!.text, 'It holds 312 items');
  assert.match(v.whyNow!, /^Today at 08:39, you asked: “帮我把那个旧账号注销掉”$/);
  assert.deepEqual(v.final, { title: 'The last step', think: 'Let me think', go: 'Go ahead' });
});

test('a time past what a Date holds does not break the card', () => {
  const v = reflectionView(typed(goodNamed('a reflection at the limits')), { lang: 'zh', ...UTC });
  assert.equal(v.recall.at(-1)!.source, '核心 某天 看到的');
});

test('quoted words cannot hide anything', () => {
  const r = { recall: [{ kind: 'confirmed', quote: 'abc‮def' }], questions: ['motive', 'later'], paths: ['snooze'] };
  const v = reflectionView(with_(RICH, r), { lang: 'zh', ...UTC });
  assert.equal(v.recall[0]!.text, 'abc⟨U+202E⟩def');
});

test('nothing in the reflection reassures or hurries', () => {
  const MECHANICAL = /签名|签|sign|hash|HIGH-RISK|Execute|reasoning|\{/i;
  const STEER = /安全|放心|没问题|别担心|不用担心|马上|赶紧|快点|立即|\bsafe\b|don'?t worry|no problem|hurry|quickly|right away|immediately/i;
  const all: CanonObject[] = V.packets.good
    .map((g: { text: string }) => pk.decode(enc.encode(g.text)))
    .filter((p: CanonObject) => p['type'] === 'GATE_2_CHALLENGE');
  assert.ok(all.length >= 3);
  for (const p of all) {
    for (const lang of ['zh', 'en'] as Lang[]) {
      const v = reflectionView(typed(p), { lang, ...UTC });
      const strings = [...v.recall.flatMap((l) => [l.text, l.source]), ...v.questions,
                       ...v.choices.flatMap((c) => [c.label, c.detail]), v.final.title, v.whyNow ?? ''];
      for (const s of strings) {
        assert.doesNotMatch(s, STEER, s);
        assert.doesNotMatch(s, MECHANICAL, s); // the page never speaks in machinery
      }
    }
  }
});

test('"I have thought it through" sends nothing; a gentler path is the only thing the reflection can send', () => {
  const ch = typed(RICH);
  assert.deepEqual(choose(ch, 'proceed'), { stage: 'final' });
  const out = choose(ch, 'export');
  assert.ok('reply' in out);
  assert.equal(canonicalText(out.reply), '{"challenge_id":"c-1","decision":"alternative","path":"export","type":"GATE_2_REPLY"}');
  assert.throws(() => choose(typed(BARE), 'export'), /not offered/); // nothing known to keep: no export
});
