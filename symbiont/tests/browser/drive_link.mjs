// Drives the built operator page in Chromium against a real node (started by
// test_browser_link.py): open the pairing link, read the check code, tap
// "connect" (a trusted click), wait for "共生体: 已连接", give a goal, answer the
// node's question from the composer, see the Gate 2 card, RELOAD in the middle
// of it, see the same card again, approve, see it done. One JSON line per
// event on stdout.
//
//   node drive_link.mjs '{"url": "...#pair=...", "shots": "/dir" | null}'
const cfg = JSON.parse(process.argv[2]);
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');
const out = (o) => process.stdout.write(JSON.stringify(o) + '\n');

const browser = await chromium.launch({
  // aiortc does not resolve mDNS host candidates; show the node real host addresses
  args: ['--disable-features=WebRtcHideLocalIpsWithMdns'],
});
const page = await (await browser.newContext({ locale: 'zh-CN', viewport: { width: 1180, height: 820 } })).newPage();
const problems = [];
const debug = [];
page.on('console', (m) => (m.type() === 'debug' ? debug.push(m.text()) : m.type() === 'error' && problems.push(m.text())));
page.on('pageerror', (e) => problems.push(e.message));
const shot = async (name) => cfg.shots && page.screenshot({ path: `${cfg.shots}/${name}.png` });
const label = () => page.textContent('#presence-label');

try {
  await page.goto(cfg.url);
  await page.waitForSelector('.pairing .code', { timeout: 30_000 });
  out({ event: 'code', code: (await page.textContent('.pairing .code')).trim(), url_after: page.url() });
  await shot('link-pairing');
  await page.click('.pairing .approve');
  await page.waitForFunction(() => document.getElementById('presence-label')?.textContent === '共生体: 已连接', null, { timeout: 30_000 });
  out({ event: 'connected', label: await label() });
  await shot('link-connected');

  await page.fill('#input', '把旧文件整理一下');
  await page.keyboard.press('Enter');
  await page.waitForSelector('.ask.state-open', { timeout: 20_000 });
  out({ event: 'question', text: (await page.textContent('.ask.state-open')).trim() });
  await page.fill('#input', 'Downloads');
  await page.keyboard.press('Enter');
  await page.waitForSelector('.consent.state-awaiting', { timeout: 20_000 });
  out({ event: 'consent', text: (await page.textContent('.consent .sentence')).trim() });
  await shot('link-consent');

  await page.reload();
  out({ event: 'reloaded' });
  await page.waitForFunction(() => document.getElementById('presence-label')?.textContent === '共生体: 已连接', null, { timeout: 40_000 });
  await page.waitForSelector('.consent.state-awaiting .approve:not([disabled])', { timeout: 30_000 });
  out({ event: 'consent-again', text: (await page.textContent('.consent .sentence')).trim() });
  await page.click('.consent .approve');
  await page.waitForSelector('.turn.say.tone-done', { timeout: 30_000 });
  out({ event: 'done', consent: (await page.getAttribute('.consent', 'class')) });
  await shot('link-done');
  const body = await page.evaluate(() => document.body.innerText);
  out({ event: 'page', text: body, traced: debug.filter((l) => l.includes('[symbiont]')).length, problems });
} catch (e) {
  await shot('link-failure');
  out({ event: 'failure', error: String(e), problems, debug: debug.slice(-40) });
  process.exitCode = 1;
} finally {
  await browser.close();
}
