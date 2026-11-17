/**
 * Gate 1 rules: what makes SecDogie push back before it plans anything.
 *
 * The first five are a port of `citadel/secdogie_citadel/socratic.py`, verdict
 * for verdict and reason for reason (fixtures/vectors/socratic.json). Python's
 * `re` is Unicode-aware where JavaScript's is not, so the patterns are
 * rewritten rather than copied:
 *   - `\b` becomes look-arounds over `[\p{L}\p{N}_]` (Python's `\w`), so
 *     "请delete文件" does not match `delete` -- in Python CJK letters are word
 *     characters;
 *   - `\s`, `\d`, `.` and `str.strip()` use Python's sets, not JavaScript's;
 *   - Python's IGNORECASE also matches İ and ı to i, which JS case folding
 *     does not, so those two are folded first;
 *   - lengths count code points, as Python's `len` does.
 *
 * The other rules read the topology graph: they refuse to guess a target the
 * graph does not hold, ask which one when several fit, and decline anything
 * that would remove mapped state (v1 has no removal).
 */

/** Python's `\s` for str patterns (str.isspace). */
const S = '[\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]';
const W = '[\\p{L}\\p{N}_]';
const B0 = `(?<!${W})`; // \b before a word character
const B1 = `(?!${W})`; // \b after a word character

const re = (src: string, flags = 'iu') => new RegExp(src, flags);

const MUTATING = re(
  `${B0}(delete|remove|write|modify|overwrite|save|install|uninstall|format|drop|rm` +
    `|post|publish|submit|send|reply|comment|tweet)${B1}` +
    '|删除|修改|覆盖|写入|保存|安装|卸载|格式化|发帖|发布|发送|回复|评论|提交',
);

const MAX_LEN = 600;

export type FindingCode =
  | 'empty'
  | 'contradiction'
  | 'unattended-posting'
  | 'polling'
  | 'overlong'
  | 'target-unproven'
  | 'target-ambiguous'
  | 'graph-destructive'
  | 'intent-unproven'
  | 'intent-contradiction';

export interface Finding {
  readonly code: FindingCode;
  /** Python's reason text for the wording rules (kept for audit parity). */
  readonly reason: string;
  readonly suggestion: string;
}

/** Python's `str.strip()`. */
export function pyStrip(s: string): string {
  return s.replace(re(`^${S}+|${S}+$`, 'gu'), '');
}

/** Case-insensitive matching the way Python's `re.IGNORECASE` does it. */
const fold = (s: string) => s.replace(/[İı]/g, 'i');

/** `int()` of a run of Unicode decimal digits (Python accepts any Nd). */
function checkEmpty(t: string): Finding | null {
  return pyStrip(t) === '' ? { code: 'empty', reason: 'empty instruction: nothing actionable', suggestion: 'State a concrete goal.' } : null;
}

function checkOverlong(t: string): Finding | null {
  if ([...t].length > MAX_LEN) {
    return {
      code: 'overlong',
      reason: 'overlong / unstructured: many steps in one instruction',
      suggestion: 'Decompose into a few named sub-goals with dependencies (a goal DAG).',
    };
  }
  return null;
}

export interface WordingReview {
  readonly verdict: 'accept' | 'revise';
  readonly findings: readonly Finding[];
  readonly reasons: readonly string[];
  readonly suggestion: string;
}

/**
 * `socratic.review`: accept, or revise with reasons and a combined suggestion. Like the Python reference it
 * looks only at the shape of the instruction (empty, too long to be one goal): what an instruction means is
 * the decision model's to understand, not a pattern's.
 */
export function reviewWording(instruction: string): WordingReview {
  const t = fold(instruction ?? '');
  const findings = [checkEmpty, checkOverlong]
    .map((c) => c(t))
    .filter((f): f is Finding => f !== null);
  return {
    verdict: findings.length ? 'revise' : 'accept',
    findings,
    reasons: findings.map((f) => f.reason),
    suggestion: findings.map((f) => f.suggestion).filter(Boolean).join(' '),
  };
}

// ---- rules over the topology graph ----------------------------------------------

/** Asking to remove or forget what has been mapped. v1 never deletes graph state. */
const GRAPH_DESTRUCTIVE = re(
  `${B0}(forget|remove|delete|drop|purge|erase|wipe|clear)${S}+(${W}+${S}+){0,3}` +
    `(map|graph|topology|routes?|states?|history)${B1}` +
    '|(忘掉|忘记|删除|删掉|清空|抹掉|清除)(这些|所有|全部)?(拓扑|路由|地图|状态图|状态|记录|图谱)',
);

export function checkGraphDestructive(instruction: string): Finding | null {
  if (!GRAPH_DESTRUCTIVE.test(fold(instruction))) return null;
  return {
    code: 'graph-destructive',
    reason: 'asks to remove mapped state: the v1 state graph is append-only and never deletes',
    suggestion: 'Narrow what is scanned from now on, or look at the mapped routes read-only.',
  };
}

/** Words that make a mutating step destructive (mirrors graph/src/plan.rs). */
export const DESTRUCTIVE_WORDS = [
  'delete', 'remove', 'destroy', 'drop', 'erase', 'purge', 'wipe', 'close', 'cancel', 'deactivate', 'terminate',
  'unsubscribe', 'revoke', '删除', '注销', '销毁',
] as const;

export function mentionsDestruction(s: string): boolean {
  const lower = s.toLowerCase();
  return DESTRUCTIVE_WORDS.some((w) => lower.includes(w));
}

export function mentionsMutation(s: string): boolean {
  return MUTATING.test(fold(s)) || /删|注销|清空|关闭|取消|撤销|改|提交|发/u.test(s) || mentionsDestruction(s);
}
