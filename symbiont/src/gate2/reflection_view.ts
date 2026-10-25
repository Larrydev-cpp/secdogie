/**
 * Gate 2's reflection, in the page's own words.
 *
 * The node sends a structured reflection (`net/packets.ts` `Reflection`): what
 * the subject carried -- the operator's own words, facts they confirmed, what
 * the node did with it, typed numbers the core observed -- plus closed sets of
 * questions and gentler paths. This module words all of it here, in the page's
 * language and the viewer's own time zone, and lays out the two stages of the
 * card:
 *
 *   1. reflect -- the headline, what it carried (each line says where it came
 *      from), why now, the questions to sit with, and the answers: the gentler
 *      paths, then "I've thought it through";
 *   2. final   -- only after "I've thought it through": the existing confirm
 *      card with exactly 再想想 / 确认放行.
 *
 * Asymmetric by construction: nothing here can make the step easier. No string
 * reassures or hurries, choosing "I've thought it through" only reveals the last
 * step (it sends nothing), and a gentler path is the only thing the reflect
 * stage can send. Quotes are the operator's own words, made `visible`.
 */

import { type Gate2Challenge, type GentlerPath, type Question, type Recall, DESTRUCTIVE_UNBIND, validate } from '../net/packets.ts';
import { type CanonObject } from '../core/canon.ts';
import { visible } from '../voice/visible.ts';

export type Lang = 'zh' | 'en';
export type ChoiceId = GentlerPath | 'proceed';

export interface RecallLine {
  readonly text: string;
  readonly source: string;
}
export interface Choice {
  readonly id: ChoiceId;
  readonly label: string;
  readonly detail: string;
}
export interface ReflectionView {
  readonly headline: string;
  readonly recall: readonly RecallLine[];
  readonly knowsNothing: boolean;
  readonly whyNow: string | null;
  readonly questions: readonly string[];
  readonly choices: readonly Choice[];
  readonly final: { readonly title: string; readonly think: string; readonly go: string };
}

export interface ViewOpts {
  readonly lang: Lang;
  readonly nowMs: number;
  /** The viewer's zone by default; tests pin one. */
  readonly timeZone?: string;
}

const DAY = 86_400_000;
const YEAR = 31_557_600_000;
const MAX_DATE_MS = 8.64e15; // ECMAScript's Date range

const WORDS = {
  zh: {
    source: { said: '你的原话', confirmed: '你确认过的', did: '它的工作记录', saw: (d: string) => `核心 ${d} 看到的` },
    said: (d: string, q: string) => `${d}，你说：『${q}』`,
    did: (n: number, d: string) => `它替你用它办过 ${n} 件事，最近一次是 ${d}`,
    joined: (d: string, y: string) => `${d} 开始用它，到现在${y}`,
    years: (y: number) => (y >= 1 ? ` ${y} 年了` : '不到一年'),
    lastActive: (r: string) => `你最近一次用它是${r}`,
    items: (n: number) => `里面有 ${n} 条内容`,
    followers: (n: number) => `有 ${n} 个人关注它`,
    whyNow: (w: string, q: string) => `${w}你说：『${q}』`,
    today: (t: string) => `今天 ${t}，`,
    on: (d: string) => `${d}，`,
    rel: (days: number) =>
      days <= 0 ? '今天' : days === 1 ? '昨天' : days < 30 ? ` ${days} 天前` : days < 365 ? ` ${Math.floor(days / 30)} 个月前` : ` ${Math.floor(days / 365)} 年前`,
    questions: {
      motive: { unbind: '你想注销它，是想放下什么？', other: '你想让它消失，是想放下什么？' },
      recent: (r: string | null) => `你${r ?? '最近'}还在用它。是什么让你今天决定放下？`,
      keep: '这些年它装下的东西，你想留一份吗？',
      later: '这一步做完就找不回来了。一个月后的你，会怎么看今天？',
    },
    choices: {
      export: { label: '想留一份，先导出', detail: '先把数据存下来，账号先不动' },
      deactivate: { label: '只是想清静，先停用', detail: '能恢复的那种，想回来随时可以' },
      snooze: { label: '放一晚，明天再问我', detail: '今天不做决定' },
      proceed: { label: '我想清楚了', detail: '看最后一步' },
    },
    final: { title: '最后一步', think: '再想想', go: '确认放行' },
  },
  en: {
    source: { said: 'your words', confirmed: 'you confirmed', did: 'its work log', saw: (d: string) => `seen by the core on ${d}` },
    said: (d: string, q: string) => `On ${d} you said: “${q}”`,
    did: (n: number, d: string) => `It did ${n} ${n === 1 ? 'thing' : 'things'} with it for you, most recently on ${d}`,
    joined: (d: string, y: string) => `In use since ${d}: ${y}`,
    years: (y: number) => (y >= 1 ? `${y} ${y === 1 ? 'year' : 'years'}` : 'less than a year'),
    lastActive: (r: string) => `You last used it ${r}`,
    items: (n: number) => `It holds ${n} ${n === 1 ? 'item' : 'items'}`,
    followers: (n: number) => `${n} ${n === 1 ? 'person follows' : 'people follow'} it`,
    whyNow: (w: string, q: string) => `${w} you asked: “${q}”`,
    today: (t: string) => `Today at ${t},`,
    on: (d: string) => `On ${d},`,
    rel: (days: number) =>
      days <= 0 ? 'today' : days === 1 ? 'yesterday' : days < 30 ? `${days} days ago` : days < 365 ? `${Math.floor(days / 30)} months ago` : `${Math.floor(days / 365)} years ago`,
    questions: {
      motive: { unbind: 'What are you hoping to let go of by deleting it?', other: 'What are you hoping to let go of by removing it?' },
      recent: (r: string | null) => `You were still using it ${r ?? 'recently'}. What made today the day?`,
      keep: 'Is there anything it holds that you want to keep a copy of?',
      later: 'Once this is done it cannot come back. How will you see today a month from now?',
    },
    choices: {
      export: { label: 'Keep a copy first', detail: 'Save its data; leave the account as it is' },
      deactivate: { label: 'I just want quiet: deactivate', detail: 'The reversible kind; you can come back' },
      snooze: { label: 'Sleep on it, ask me tomorrow', detail: 'No decision today' },
      proceed: { label: "I've thought it through", detail: 'Show me the last step' },
    },
    final: { title: 'The last step', think: 'Let me think', go: 'Go ahead' },
  },
} as const;

function fmt(lang: Lang, timeZone: string | undefined) {
  const locale = lang === 'zh' ? 'zh-CN' : 'en-GB';
  const date = new Intl.DateTimeFormat(locale, { year: 'numeric', month: 'long', day: 'numeric', timeZone });
  const time = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', hour12: false, timeZone });
  const day = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone });
  // The wire allows any safe integer; a Date holds less. A valid packet must
  // never break the card, so a time out of range reads as "some day".
  const ok = (ms: number) => Math.abs(ms) <= MAX_DATE_MS;
  const someDay = lang === 'zh' ? '某天' : 'some day';
  return {
    date: (ms: number) => (ok(ms) ? date.format(ms) : someDay),
    time: (ms: number) => (ok(ms) ? time.format(ms) : someDay),
    sameDay: (a: number, b: number) => ok(a) && ok(b) && day.format(a) === day.format(b),
  };
}

/** The reflection of a validated challenge, worded for the page. */
export function reflectionView(ch: Gate2Challenge, opts: ViewOpts): ReflectionView {
  const w = WORDS[opts.lang];
  const f = fmt(opts.lang, opts.timeZone);
  const r = ch.reflection;
  const daysAgo = (ms: number) => Math.floor((opts.nowMs - ms) / DAY);
  const q = (s: string) => visible(s);

  const line = (m: Recall): RecallLine => {
    switch (m.kind) {
      case 'said':
        return { text: w.said(f.date(m.at_ms), q(m.quote)), source: w.source.said };
      case 'confirmed':
        return { text: q(m.quote), source: w.source.confirmed };
      case 'did':
        return { text: w.did(m.count, f.date(m.last_at_ms)), source: w.source.did };
      case 'saw': {
        const src = w.source.saw(f.date(m.at_ms));
        if (m.fact === 'joined_at') {
          return { text: w.joined(f.date(m.value), w.years(Math.floor((opts.nowMs - m.value) / YEAR))), source: src };
        }
        if (m.fact === 'last_active_at') return { text: w.lastActive(w.rel(daysAgo(m.value))), source: src };
        if (m.fact === 'items') return { text: w.items(m.value), source: src };
        return { text: w.followers(m.value), source: src };
      }
    }
  };

  const lastActive = r.recall.find((m): m is Extract<Recall, { kind: 'saw' }> => m.kind === 'saw' && m.fact === 'last_active_at');
  const question = (id: Question): string => {
    if (id === 'motive') return ch.action_type === DESTRUCTIVE_UNBIND ? w.questions.motive.unbind : w.questions.motive.other;
    if (id === 'recent') return w.questions.recent(lastActive ? w.rel(daysAgo(lastActive.value)) : null);
    return w.questions[id];
  };

  let whyNow: string | null = null;
  if (r.why_now) {
    const at = r.why_now.at_ms;
    const when = f.sameDay(at, opts.nowMs) ? w.today(f.time(at)) : w.on(f.date(at));
    whyNow = w.whyNow(when, q(r.why_now.quote));
  }

  const choices: Choice[] = [...r.paths, 'proceed' as const].map((id) => ({ id, ...w.choices[id] }));
  return {
    headline: q(ch.emotional_memory),
    recall: r.recall.map(line),
    knowsNothing: r.recall.length === 0,
    whyNow,
    questions: r.questions.map(question),
    choices,
    final: w.final,
  };
}

/** What a choice in the reflect stage does: a gentler path is sent as a
 * `GATE_2_REPLY` (validated, so only a path in the closed set can leave);
 * "I've thought it through" sends nothing and only reveals the final step. A
 * path the reflection did not offer is refused here as well as at the node. */
export function choose(ch: Gate2Challenge, choice: ChoiceId): { readonly stage: 'final' } | { readonly reply: CanonObject } {
  if (choice === 'proceed') return { stage: 'final' };
  if (!ch.reflection.paths.includes(choice)) throw new Error('that path was not offered');
  const reply: CanonObject = { type: 'GATE_2_REPLY', challenge_id: ch.challenge_id, decision: 'alternative', path: choice };
  return { reply: validate(reply, 'to-node') };
}
