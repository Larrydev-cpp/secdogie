// The built operator page in Chromium, served with its real headers
// (scripts/serve.mjs over site/):
//  - unpaired: says so, and opens no socket at all;
//  - the demo, in Chinese and in English, desktop and phone: Gate 1, Gate 2
//    (Approve armed after a moment), done -- with nothing mechanical in the DOM;
//  - paired but the computer is away: "暂时联系不上", and it keeps trying;
//  - the security headers are there, and the page refuses to live in a frame.
// Screenshots go to $SECDOGIE_SHOTS (default: a temp dir). Exit 1 on any failure.
//
//   npm run build && node scripts/site.mjs && node tests/browser/check_page.mjs
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');
const root = fileURLToPath(new URL('../..', import.meta.url));
const shots = process.env.SECDOGIE_SHOTS ?? mkdtempSync(join(tmpdir(), 'secdogie-shots-'));
mkdirSync(shots, { recursive: true });

const server = spawn('node', ['scripts/serve.mjs', '--port', '0'], { cwd: root, stdio: ['ignore', 'pipe', 'inherit'] });
const base = await new Promise((resolve) => server.stdout.once('data', (d) => resolve(/http:\/\/127\.0\.0\.1:\d+\//.exec(String(d))[0])));
const browser = await chromium.launch();
const failures = [];
const check = (ok, what) => {
  if (!ok) failures.push(what);
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`);
};

const MACHINERY = /[0-9a-f]{64}|did:key:|element:\d|challenge|action_hash|\bsig\b/;

async function machineryFree(page, label) {
  const text = await page.evaluate(() => {
    const parts = [];
    for (const el of document.body.querySelectorAll('*')) {
      if (el.closest('.quote')) continue;
      for (const a of el.attributes) parts.push(a.value);
      for (const n of el.childNodes) if (n.nodeType === 3) parts.push(n.textContent);
    }
    return parts.join('\n');
  });
  check(!MACHINERY.test(text), `${label}: nothing mechanical on the page`);
}

async function open(locale, viewport, hash = '') {
  const ctx = await browser.newContext({ locale, viewport });
  const page = await ctx.newPage();
  const problems = [];
  const sockets = [];
  page.on('pageerror', (e) => problems.push(e.message));
  page.on('console', (m) => m.type() === 'error' && problems.push(m.text()));
  page.on('websocket', (ws) => sockets.push(ws.url()));
  const res = await page.goto(base + hash);
  return { ctx, page, problems, sockets, res };
}

async function demo(locale, viewport, tag, goal, words) {
  const { ctx, page, problems } = await open(locale, viewport, '#demo');
  await page.waitForFunction((l) => document.getElementById('presence-label')?.textContent === l, words.label);
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(shots, `${tag}-1-idle.png`) });
  await page.fill('#input', goal);
  await page.keyboard.press('Enter');
  await page.waitForSelector('.ask.state-open .chip');
  await page.waitForTimeout(250);
  check((await page.textContent('.ask .lead')) === words.gate1, `${tag}: the Gate 1 question is the gentle one`);
  await page.screenshot({ path: join(shots, `${tag}-2-gate1.png`) });
  await page.click('.ask .chip >> nth=0');
  await page.waitForSelector('.consent.state-awaiting');
  const disabledAtFirst = await page.$eval('.consent .approve', (b) => b.disabled);
  check(disabledAtFirst, `${tag}: Approve is not live the moment the card appears`);
  await page.waitForSelector('.consent .approve:not([disabled])');
  await page.waitForTimeout(400);
  check((await page.textContent('.consent .sentence')) === words.gate2, `${tag}: the Gate 2 sentence`);
  const buttons = await page.$$eval('.consent button', (bs) => bs.map((b) => b.textContent));
  check(JSON.stringify(buttons) === JSON.stringify(words.buttons), `${tag}: Gate 2 has exactly ${words.buttons.join(' / ')}`);
  await machineryFree(page, tag);
  await page.screenshot({ path: join(shots, `${tag}-3-gate2.png`) });
  await page.click('.consent .approve');
  await page.waitForSelector('.consent.state-done');
  await page.waitForTimeout(250);
  await page.screenshot({ path: join(shots, `${tag}-4-done.png`) });
  check(problems.length === 0, `${tag}: no page errors (${problems.join(' | ')})`);
  await ctx.close();
}

const desktop = { width: 1180, height: 820 };
const phone = { width: 390, height: 844 };
const ZH = {
  label: '共生体: 演示',
  gate1: '刚才找到了两个相似的地方，帮你确认一下是这个吗？',
  gate2: '这一步会直接注销旧账号（在 docs.example.com），做完就没法恢复了。确认要继续吗？',
  buttons: ['取消', '批准'],
};
const EN = {
  label: 'Symbiont: Demo',
  gate1: 'I found two similar places. Is it this one?',
  gate2: 'This step will close the old account (on docs.example.com), and it cannot be undone. Are you sure you want to proceed?',
  buttons: ['Cancel', 'Approve'],
};

try {
  // -- unpaired: says so, and reaches nothing
  {
    const { ctx, page, sockets, res } = await open('zh-CN', desktop);
    const h = res.headers();
    check(/frame-ancestors 'none'/.test(h['content-security-policy'] ?? ''), 'CSP header with frame-ancestors');
    check(h['x-frame-options'] === 'DENY' && h['cross-origin-opener-policy'] === 'same-origin', 'X-Frame-Options and COOP');
    check(h['referrer-policy'] === 'no-referrer' && /camera=\(\)/.test(h['permissions-policy'] ?? ''), 'Referrer-Policy and Permissions-Policy');
    await page.waitForFunction(() => document.getElementById('presence-label')?.textContent === '共生体: 未配对');
    await page.waitForTimeout(500);
    check(sockets.length === 0, 'unpaired: no socket opened');
    check((await page.textContent('#ambient')).includes('secdogie-node pair'), 'unpaired: says how to pair');
    await page.screenshot({ path: join(shots, 'zh-0-unpaired.png') });
    await ctx.close();
  }
  // -- the demo, both languages, desktop and phone
  await demo('zh-CN', desktop, 'zh-desktop', '把旧账号注销掉', ZH);
  await demo('en-US', desktop, 'en-desktop', 'close the old account', EN);
  await demo('zh-CN', phone, 'zh-phone', '把旧账号注销掉', ZH);
  await demo('en-GB', phone, 'en-phone', 'close the old account', EN);
  // -- paired, but the computer is away: it says so and keeps trying (no address shown anywhere)
  {
    const { ctx, page } = await open('zh-CN', desktop);
    await page.evaluate(async () => {
      const req = indexedDB.open('secdogie-symbiont', 1);
      req.onupgradeneeded = () => {
        req.result.createObjectStore('keys');
        req.result.createObjectStore('pairing');
      };
      const db = await new Promise((r) => (req.onsuccess = () => r(req.result)));
      const tx = db.transaction('pairing', 'readwrite');
      tx.objectStore('pairing').put({ v: 1, node: 'did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK', room: 'nobody-home-0001', app: '', operator: '', pairedAt: 1 }, 'node/v1');
      await new Promise((r) => (tx.oncomplete = r));
    });
    await page.reload();
    await page.waitForFunction(() => document.getElementById('presence-label')?.textContent === '共生体: 暂时联系不上', null, { timeout: 15_000 });
    await machineryFree(page, 'unreachable');
    await page.screenshot({ path: join(shots, 'zh-5-unreachable.png') });
    await ctx.close();
  }
  // -- it will not live inside another page
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.setContent(`<iframe src="${base}#demo" width="600" height="400"></iframe>`);
    await page.waitForTimeout(1500);
    const frame = page.frames()[1];
    const inside = frame ? await frame.evaluate(() => document.getElementById('presence-label')?.textContent ?? '').catch(() => '') : '';
    check(inside === '', 'framed: the browser refuses to show it in a frame');
    await ctx.close();
  }
} catch (e) {
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
  server.kill();
}
console.log(`screenshots: ${shots}`);
if (failures.length) {
  console.log(`${failures.length} check(s) failed`);
  process.exit(1);
}
