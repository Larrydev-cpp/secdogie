/**
 * From words to candidate targets in the mapped topology -- deterministically.
 *
 * An interpretation never names a target the graph does not hold: it only
 * ranks the graph's own states against the words of the instruction. When
 * nothing fits, Gate 1 asks; when several fit equally, Gate 1 asks which.
 * A model-backed interpreter can replace {@link LexicalInterpreter} behind the
 * same interface, under the same rule: choose among `topology.targets`, never
 * add to them.
 */

import { mentionsDestruction, mentionsMutation } from './rules.ts';

export interface TopologyTarget {
  readonly stateKey: string;
  readonly origin: string;
  readonly route: string;
  readonly queryKeys: readonly string[];
  /** The form the markup says submits to this state, if any. */
  readonly form: { readonly method: 'get' | 'post'; readonly fields: readonly string[] } | null;
}

export interface TopologySnapshot {
  readonly targets: readonly TopologyTarget[];
}

export type Verb = 'navigate' | 'submit';

export interface Interpretation {
  readonly verb: Verb;
  readonly matches: readonly TopologyTarget[];
}

export interface IntentInterpreter {
  interpret(instruction: string, topology: TopologySnapshot, opts?: { verb?: Verb }): Interpretation;
}

/** Common Chinese words mapped onto the route vocabulary of most sites. */
const SYNONYMS: ReadonlyArray<readonly [string, string]> = [
  ['账号', 'account'], ['账户', 'account'], ['帐号', 'account'], ['删除', 'delete'], ['删掉', 'delete'],
  ['注销', 'delete'], ['删', 'delete'], ['设置', 'settings'], ['搜索', 'search'], ['个人资料', 'profile'],
  ['资料', 'profile'], ['登录', 'login'], ['退订', 'unsubscribe'], ['订阅', 'subscribe'], ['文档', 'docs'],
  ['指南', 'guide'], ['安装', 'install'], ['价格', 'pricing'], ['帮助', 'help'], ['评论', 'comment'],
];

const words = (s: string): string[] =>
  s.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((w) => w.length > 1 && /^[\x21-\x7e]+$/.test(w));

export function instructionTokens(instruction: string): Set<string> {
  const out = new Set(words(instruction));
  for (const [zh, en] of SYNONYMS) if (instruction.includes(zh)) out.add(en);
  return out;
}

export function targetTokens(t: TopologyTarget): Set<string> {
  const host = t.origin.replace(/^https:\/\//, '').replace(/:\d+$/, '');
  return new Set([
    ...t.route.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean),
    ...(t.form?.fields ?? []).flatMap((f) => f.toLowerCase().split(/[^a-z0-9]+/)),
    ...host.split('.').filter((l) => l.length > 1),
    host,
  ]);
}

/** Words that ask to look, not to change ("打开安装指南" is not an install). */
const NAVIGATE = /打开|查看|看看|看一下|浏览|阅读|\b(open|view|show|read|browse|visit)\b|go to/iu;

/** Destruction always means submit; other change words do unless the request asks to look. */
export function verbOf(instruction: string): Verb {
  if (mentionsDestruction(instruction)) return 'submit';
  return mentionsMutation(instruction) && !NAVIGATE.test(instruction) ? 'submit' : 'navigate';
}

export class LexicalInterpreter implements IntentInterpreter {
  interpret(instruction: string, topology: TopologySnapshot, opts: { verb?: Verb } = {}): Interpretation {
    const verb: Verb = opts.verb ?? verbOf(instruction);
    const want = instructionTokens(instruction);
    const pool = verb === 'submit' ? topology.targets.filter((t) => t.form !== null) : topology.targets;
    let best = 0;
    let matches: TopologyTarget[] = [];
    for (const t of pool) {
      const have = targetTokens(t);
      let score = 0;
      for (const w of want) if (have.has(w)) score++;
      if (score > best) {
        best = score;
        matches = [t];
      } else if (score === best && score > 0) {
        matches.push(t);
      }
    }
    // A host name alone ("on docs.example.com") is not a target.
    const meaningful = matches.filter((t) => {
      const route = new Set(t.route.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
      const fields = new Set((t.form?.fields ?? []).map((f) => f.toLowerCase()));
      return [...want].some((w) => route.has(w) || fields.has(w));
    });
    return { verb, matches: meaningful };
  }
}

/** How a target is named to a person: route, then where, then what kind. */
export function targetLabel(t: TopologyTarget): string {
  const host = t.origin.replace(/^https:\/\//, '');
  const q = t.queryKeys.length ? `?${t.queryKeys.join('&')}` : '';
  const kind = t.form ? `${t.form.method.toUpperCase()} 表单` : '页面';
  return `${host}${t.route}${q}（${kind}）`;
}
