// Three of your own devices go online and become one mesh, over real WebRTC
// data channels between real browsers (S5, docs/PLAN-OWN-DEVICES.zh.md).
//
//   node tests/e2e/mesh.e2e.cjs [--shots <dir>]
//
// Each device is its own browser context (its own keys). They pair through
// pair.html first; then each opens mesh.html. What it holds the page to:
//   * nothing connects until the person clicks 上线;
//   * the phone, which at first knows only the owner, learns of the tablet
//     from the owner's roster and links to it directly: a full mesh;
//   * a line said on one device shows on the others;
//   * a device that goes offline, or whose page closes, shows offline on the rest.
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const { pair, phase, relay, serve, watchConsole } = require('./harness.cjs');

const shotsAt = process.argv.includes('--shots') ? process.argv[process.argv.indexOf('--shots') + 1] : null;

const onlineCount = (page) => page.$$eval('#devices li[data-state="online"]', (els) => els.length);
const waitOnline = (page, n, timeout = 30_000) => page.waitForFunction(
  (want) => document.querySelectorAll('#devices li[data-state="online"]').length === want, n, { timeout });
const stateOf = (page, did) => page.$eval(`#devices li[data-did="${did}"]`, (el) => el.dataset.state);
const myDid = (page) => page.$eval('#me-did', (el) => el.title);

async function shot(page, name) {
  if (!shotsAt) return;
  fs.mkdirSync(shotsAt, { recursive: true });
  await page.screenshot({ path: path.join(shotsAt, `${name}.png`), fullPage: true });
}

(async () => {
  const server = await serve();
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  const net = relay();
  const pageErrors = [];
  const pages = [];
  const device = async (name, viewport) => {
    const context = await browser.newContext({ locale: 'zh-CN', viewport, colorScheme: 'dark' });
    const page = await context.newPage();
    page.on('pageerror', (err) => pageErrors.push(err.message));
    watchConsole(page, name);
    pages.push(page);
    await net.attach(page);
    return page;
  };
  let failed = false;
  try {
    const owner = await device('owner', { width: 1280, height: 900 });
    const phone = await device('phone', { width: 390, height: 844 });
    const tablet = await device('tablet', { width: 820, height: 1180 });
    await pair(owner, phone, { origin, name: '我的手机', ownerName: '书房的电脑' });
    await pair(owner, tablet, { origin, name: '平板' });
    console.log('ok  paired a phone and a tablet');

    // ---- everyone opens 我的设备: nothing connects before 上线 -------------------------
    const signal = encodeURIComponent(`${origin.replace('http', 'ws')}/ws`);
    const opened = new Map();
    for (const p of [owner, phone, tablet]) {
      await p.goto(`${origin}/mesh.html?debug&signal=${signal}`);
      await phase(p, 'offline');
      opened.set(p, net.opened(p));
    }
    await owner.waitForTimeout(500);
    for (const p of [owner, phone, tablet]) assert.equal(net.opened(p), opened.get(p), 'a page connected before 上线');
    assert.equal(await phone.$$eval('#devices li', (els) => els.length), 1, 'a new phone knows only the owner');
    assert.equal(await owner.$$eval('#devices li', (els) => els.length), 2);
    await shot(phone, 'phone-1-offline');

    // ---- 上线: a full mesh, the phone finding the tablet through the owner's roster ----
    for (const p of [owner, phone, tablet]) {
      await p.click('#online');
      await phase(p, 'online');
    }
    await Promise.all([waitOnline(owner, 2), waitOnline(phone, 2), waitOnline(tablet, 2)]);
    const [ownerDid, phoneDid, tabletDid] = [await myDid(owner), await myDid(phone), await myDid(tablet)];
    assert.equal(await stateOf(phone, tabletDid), 'online');
    assert.equal(await stateOf(tablet, phoneDid), 'online');
    assert.match(await phone.textContent(`#devices li[data-did="${ownerDid}"]`), /书房的电脑/);
    assert.match(await owner.textContent(`#devices li[data-did="${tabletDid}"]`), /平板/);
    await owner.waitForFunction(() => /毫秒/.test(document.querySelector('#devices').textContent), null, { timeout: 15_000 });
    await shot(owner, 'owner-2-online');
    await shot(phone, 'phone-2-online');
    console.log('ok  three devices online as a full mesh; the phone found the tablet through the roster');

    // ---- a line said on the tablet shows on the others ---------------------------------
    await tablet.fill('#say', '平板问好');
    await tablet.click('#send');
    for (const p of [owner, phone]) {
      await p.waitForFunction(() => /平板问好/.test(document.querySelector('#notes').textContent), null, { timeout: 10_000 });
    }
    assert.match(await phone.textContent('#notes'), /平板/);
    await shot(phone, 'phone-3-note');
    console.log('ok  a line from the tablet reached the desktop and the phone');

    // ---- 下线, and a closed page: the others see them go -------------------------------
    await phone.click('#online');
    await phase(phone, 'offline');
    for (const p of [owner, tablet]) {
      await p.waitForFunction((did) => document.querySelector(`#devices li[data-did="${did}"]`)?.dataset.state === 'offline',
        phoneDid, { timeout: 20_000 });
    }
    await tablet.close();
    await owner.waitForFunction((did) => document.querySelector(`#devices li[data-did="${did}"]`)?.dataset.state === 'offline',
      tabletDid, { timeout: 20_000 });
    assert.equal(await onlineCount(owner), 0);
    await shot(owner, 'owner-4-alone');
    console.log('ok  a device that goes offline, or closes its page, shows offline on the rest');
    assert.deepEqual(pageErrors, [], 'a page threw');
  } catch (err) {
    failed = true;
    console.error(err);
    for (const p of pages) if (!p.isClosed()) console.error(await p.diagnose());
  } finally {
    await browser.close();
    server.close();
  }
  process.exit(failed ? 1 : 0);
})();
