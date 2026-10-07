// The red lines, as a test (like dialogue/tests/test_purity.py): the source
// may not contain a blocking modal, a focus grab, markup injection, dynamic
// code, a credentialed or redirect-following fetch, media capture, or storage
// -- with two named exceptions: IndexedDB in net/keystore.ts (the two keys and
// the pairing record, nothing else), and console in core/trace.ts (DevTools).
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('../src', import.meta.url));

const FORBIDDEN: Array<[RegExp, string]> = [
  [/\.focus\(/, 'focus grab'],
  [/showModal|role=["']dialog|aria-modal|<dialog/, 'modal dialog'],
  [/\b(alert|confirm|prompt)\(/, 'blocking browser dialog'],
  [/innerHTML|outerHTML|insertAdjacentHTML|document\.write/, 'markup injection'],
  [/\beval\(|new Function\(/, 'dynamic code'],
  [/credentials:\s*['"](include|same-origin)/, 'credentialed fetch'],
  [/redirect:\s*['"]follow/, 'redirect following'],
  [/localStorage|sessionStorage|document\.cookie/, 'storage or cookies'],
  [/getUserMedia|getDisplayMedia|addTrack|MediaStream/, 'camera, microphone or screen capture'],
  [/\bautofocus\b/, 'focus grab'],
  [/importScripts\(/, 'remote script import'],
  [/window\.open\(/, 'pop-up window'],
  [/requestFullscreen|requestPointerLock/, 'taking over the screen'],
];

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? files(join(dir, e.name)) : e.name.endsWith('.ts') ? [join(dir, e.name)] : [],
  );
}

/** Source without comments, so a doc comment may name what the code never does. */
function code(path: string): string {
  return readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

/** Allowed in exactly one file each. */
const CONFINED: Array<[RegExp, string, string]> = [
  [/indexedDB/, 'net/keystore.ts', 'IndexedDB'],
  [/\bconsole\./, 'core/trace.ts', 'console output'],
];

test('no source file crosses a red line', () => {
  const found: string[] = [];
  for (const f of files(SRC)) {
    const c = code(f);
    const rel = f.slice(SRC.length + 1).replaceAll('\\', '/');
    for (const [re, what] of FORBIDDEN) if (re.test(c)) found.push(`${rel}: ${what}`);
    for (const [re, only, what] of CONFINED) if (re.test(c) && rel !== only) found.push(`${rel}: ${what} outside ${only}`);
  }
  assert.deepEqual(found, []);
});

test('the keystore opens only its two stores and keeps no conversation', () => {
  const c = code(join(SRC, 'net', 'keystore.ts'));
  const stores = [...c.matchAll(/createObjectStore\(\s*['"]([^'"]+)['"]/g)].map((m) => m[1]).sort();
  assert.deepEqual(stores, ['keys', 'pairing']);
  assert.doesNotMatch(c, /conversation|transcript|goal|answer/i);
});

test('the scan would catch a violation', () => {
  const probe = "el.focus(); x.innerHTML = s; fetch(u, { credentials: 'include', redirect: 'follow' })";
  const hits = FORBIDDEN.filter(([re]) => re.test(probe)).map(([, w]) => w);
  assert.deepEqual(hits, ['focus grab', 'markup injection', 'credentialed fetch', 'redirect following']);
});

test('the page shell: no inline code or style, no address fields, nothing that grabs focus', () => {
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)[^>]*>/, 'inline script');
  assert.doesNotMatch(html, /\bstyle=|<style/, 'inline style');
  assert.doesNotMatch(html, /\bautofocus\b|\bon[a-z]+=/i, 'autofocus or inline handler');
  assert.doesNotMatch(html, /type=["']?(url|range)|<select|<aside/i, 'address fields, sliders or side panels');
  assert.match(html, /Content-Security-Policy[^>]*script-src 'self'/);
  assert.equal([...html.matchAll(/<button/g)].length, 2, 'send and stop: the only fixed buttons');
});
