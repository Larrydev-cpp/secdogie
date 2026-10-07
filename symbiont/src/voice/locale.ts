/**
 * Chinese first, English automatically: the first of the browser's languages
 * that is Chinese or English decides; neither means Chinese.
 */

import type { Dict } from './dict.ts';
import { en } from './en.ts';
import { zh } from './zh.ts';

export function pickLanguage(languages: readonly string[]): 'zh' | 'en' {
  for (const l of languages) {
    const t = l.toLowerCase();
    if (t === 'zh' || t.startsWith('zh-')) return 'zh';
    if (t === 'en' || t.startsWith('en-')) return 'en';
  }
  return 'zh';
}

export function voiceFor(languages: readonly string[]): Dict {
  return pickLanguage(languages) === 'en' ? en : zh;
}

export { en, zh };
