// The page's words: Chinese first, English automatically; every finding the
// node or the page can raise has a gentle line in both; the Gate 2 sentence is
// made from the signed fields alone; signed content is never cut and nothing
// in it can hide.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import type { TargetAction } from '../src/gate2/authz.ts';
import { DialogueType, dialoguePacket } from '../src/gate2/wire.ts';
import { consentView } from '../src/voice/consent.ts';
import { FINDING_CODES } from '../src/voice/dict.ts';
import { en, pickLanguage, voiceFor, zh } from '../src/voice/locale.ts';
import { askView } from '../src/voice/question.ts';
import { readStatus, statusSentence } from '../src/voice/status.ts';
import { visible } from '../src/voice/visible.ts';

const link = JSON.parse(readFileSync(new URL('../../fixtures/vectors/link.json', import.meta.url), 'utf8'));

test('Chinese unless the first Chinese-or-English language is English', () => {
  assert.equal(pickLanguage(['zh-CN', 'en-US']), 'zh');
  assert.equal(pickLanguage(['en-GB', 'zh']), 'en');
  assert.equal(pickLanguage(['ja-JP', 'fr', 'en']), 'en');
  assert.equal(pickLanguage(['ja-JP', 'fr']), 'zh');
  assert.equal(pickLanguage([]), 'zh');
  assert.equal(voiceFor(['en']).lang, 'en');
});

test("every finding the node's gate can raise, and every one the page's rules can, has a line in both languages", () => {
  for (const code of link.finding_kinds) assert.ok((FINDING_CODES as readonly string[]).includes(code), code);
  for (const code of FINDING_CODES) {
    assert.ok(zh.ask.finding[code], `zh ${code}`);
    assert.ok(en.ask.finding[code], `en ${code}`);
  }
  assert.equal(zh.ask.finding['target-ambiguous'], '刚才找到了两个相似的地方，帮你确认一下是这个吗？');
});

test('the header labels are the spec words', () => {
  assert.equal(zh.presence.connected, '共生体: 已连接');
  assert.equal(en.presence.connected, 'Symbiont: Connected');
  for (const d of [zh, en]) for (const v of [...Object.values(d.presence), ...Object.values(d.presenceHint)]) assert.doesNotMatch(v, /\d|did:key/);
});

test('Gate 2: the sentence comes from the signed fields, and severity only goes up', () => {
  const close: TargetAction = { kind: 'submit', target_id: 'https://docs.example.com/account/delete', target_role: 'form', target_name: '注销旧账号', text: '', high_risk: true };
  const v = consentView(zh, close);
  assert.equal(v.sentence, '这一步会直接注销旧账号（在 docs.example.com），做完就没法恢复了。确认要继续吗？');
  assert.equal(v.grave, true);
  // a Delete key is grave even when the node did not flag it
  const del = consentView(zh, { kind: 'key', target_id: '', target_role: '', target_name: '', text: 'delete', high_risk: false });
  assert.equal(del.sentence, '这一步会按下 Delete，做完就没法恢复了。确认要继续吗？');
  // a harmless-looking click on a deleting button is grave too
  assert.ok(consentView(en, { kind: 'left_click', target_id: '', target_role: 'button', target_name: 'Delete repository', text: '', high_risk: false }).grave);
  // a real node's element ids never reach the words
  const blind = consentView(zh, { kind: 'left_click', target_id: 'element:42', target_role: '', target_name: '', text: '', high_risk: true });
  assert.equal(blind.sentence, '这一步会在屏幕上点一下，做完就没法恢复了。确认要继续吗？');
  assert.doesNotMatch(JSON.stringify(blind), /element:42/);
  assert.equal(consentView(en, close).sentence.endsWith('Are you sure you want to proceed?'), true);
});

test('signed content is shown whole, with hidden characters made visible', () => {
  const tricky = `rm -rf ~/old\n${'a'.repeat(64)} did:key:z6Mkfake‮txt.exe‍\t!`;
  const v = consentView(zh, { kind: 'type', target_id: '', target_role: 'textbox', target_name: 'Terminal', text: tricky, high_risk: true });
  const text = v.quote.find((q) => q.label === zh.consent.quoteLabel.text)!.text;
  assert.ok(text.includes('a'.repeat(64)) && text.includes('did:key:z6Mkfake'));
  assert.ok(text.includes('⏎\n') && text.includes('⟨U+202E⟩') && text.includes('⟨U+200D⟩') && text.includes('⇥'));
  assert.equal(visible('\u0000\u007f'), '␀␡');
  const huge = consentView(zh, { kind: 'type', target_id: '', target_role: '', target_name: '', text: 'x'.repeat(5000), high_risk: true });
  assert.equal(huge.tooLong, true);
  assert.equal(huge.quote[0]!.text.length, 5000, 'never cut, refused instead');
});

test('Gate 1: the loop prompts are reworded; the wire values stay exact', () => {
  const step = askView(zh, dialoguePacket({ probe_id: 'p', dialogue_type: DialogueType.SocraticQuestion, content: 'Execute HIGH-RISK key(delete)?', suggested_options: ['Approve', 'Deny'] }));
  assert.equal(step.question, '接下来要按下 Delete，可以吗？');
  assert.deepEqual(step.options, [{ label: '可以', value: 'Approve' }, { label: '先不要', value: 'Deny' }]);
  const plan = askView(en, dialoguePacket({ probe_id: 'p', dialogue_type: DialogueType.SocraticQuestion, content: 'approve plan: 1. open\n2. tidy' }));
  assert.equal(plan.question, en.ask.confirmPlan);
  assert.equal(plan.quote[0]!.text, '1. open⏎\n2. tidy');
  const model = askView(zh, dialoguePacket({ probe_id: 'p', dialogue_type: DialogueType.SocraticQuestion, content: 'Which folder?', gate_finding: 'intent-unproven' }));
  assert.equal(model.lead, zh.ask.finding['intent-unproven']);
  assert.equal(model.question, 'Which folder?');
  assert.equal(askView(zh, dialoguePacket({ probe_id: 'p', dialogue_type: DialogueType.SocraticQuestion, content: '', gate_finding: 'made-up' })).lead, '');
});

test('CURRENT_STATUS and the other status lines', () => {
  assert.deepEqual(readStatus('status: waiting for operator', 'g-1'), { kind: 'current', running: true, waiting: true, goal: 'g-1' });
  assert.deepEqual(readStatus('status: idle', ''), { kind: 'current', running: false, waiting: false, goal: '' });
  const f = readStatus('goal g-1 finished: exit 0 -- tidied 3 files', '');
  assert.deepEqual(f, { kind: 'finished', goal: 'g-1', ok: true, detail: 'tidied 3 files' });
  assert.equal(statusSentence(zh, f)!.text, '做完了。');
  assert.equal(statusSentence(zh, readStatus('refused: add_goal is not supported', 'r'))!.text, zh.reply.refused);
  assert.equal(statusSentence(zh, readStatus('accepted: goal g queued', 'r'))!.text, zh.reply.accepted);
  assert.equal(statusSentence(zh, readStatus('accepted: stop requested for g', 'r'))!.text, zh.reply.stopped);
});
