// The red lines, as a test (like dialogue/tests/test_purity.py): the source
// may not contain a blocking modal, a focus grab, markup injection, dynamic
// code, a credentialed or redirect-following fetch, or storage of anything.
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
  [/localStorage|sessionStorage|indexedDB|document\.cookie/, 'storage or cookies'],
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

test('no source file crosses a red line', () => {
  const found: string[] = [];
  for (const f of files(SRC)) {
    const c = code(f);
    for (const [re, what] of FORBIDDEN) if (re.test(c)) found.push(`${f.slice(SRC.length + 1)}: ${what}`);
  }
  assert.deepEqual(found, []);
});

test('the scan would catch a violation', () => {
  const probe = "el.focus(); x.innerHTML = s; fetch(u, { credentials: 'include', redirect: 'follow' })";
  const hits = FORBIDDEN.filter(([re]) => re.test(probe)).map(([, w]) => w);
  assert.deepEqual(hits, ['focus grab', 'markup injection', 'credentialed fetch', 'redirect following']);
});
