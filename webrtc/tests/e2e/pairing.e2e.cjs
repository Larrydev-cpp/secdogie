// Two real browsers pair over a real WebRTC data channel.
//
//   node tests/e2e/pairing.e2e.cjs [--shots <dir>]
//
// Each device is its own browser context (its own IndexedDB, its own keys).
// The test relays the signaling itself (Playwright's WebSocket routing,
// speaking the gateway's protocol), so nothing leaves this machine: the data
// channel runs over loopback. Needs the `playwright` package and Chromium.
//
// What it holds the page to:
//   * the joining page opens no connection of any kind before 同意 is clicked;
//   * both screens show the same six digits;
//   * after 接纳 the device holds an admission any node would verify;
//   * 不一样 admits nothing;
//   * a link whose invitation was altered is refused, with no agree button;
//   * opened on a plain-http LAN address, the page says why it cannot work.
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const { phase, relay, serve, watchConsole } = require('./harness.cjs');

const shotsAt = process.argv.includes('--shots') ? process.argv[process.argv.indexOf('--shots') + 1] : null;

const code = (page) => page.$eval('#code', (el) => el.textContent.replace(/\D/g, ''));

async function shot(page, name) {
  if (!shotsAt) return;
  fs.mkdirSync(shotsAt, { recursive: true });
  await page.screenshot({ path: path.join(shotsAt, `${name}.png`), fullPage: true });
}

(async () => {
  const server = await serve();
  const origin = `http://127.0.0.1:${server.address().port}`;
  // lan.test plays a LAN address over plain http: not a secure context, unlike 127.0.0.1
  const browser = await chromium.launch({ args: ['--host-resolver-rules=MAP lan.test 127.0.0.1'] });
  const net = relay();
  const pageErrors = [];
  const pages = [];
  const device = async (viewport, { locale = 'zh-CN', colorScheme = 'dark' } = {}) => {
    const context = await browser.newContext({ locale, viewport, colorScheme });
    const page = await context.newPage();
    page.on('pageerror', (err) => pageErrors.push(`${err.message}`));
    watchConsole(page, `device ${pages.length + 1}`);
    pages.push(page);
    await net.attach(page);
    return page;
  };
  let failed = false;
  try {
    // ---- the inviting device writes an invitation --------------------------------------
    const owner = await device({ width: 1280, height: 900 });
    await owner.goto(`${origin}/pair.html?debug&signal=${encodeURIComponent(origin.replace('http', 'ws'))}/ws`);
    await phase(owner, 'form');
    await shot(owner, 'owner-1-form');
    await owner.fill('#owner-name', '书房的电脑');
    await owner.click('#write');
    await phase(owner, 'waiting');
    const link = await owner.inputValue('#link');
    assert.match(link, /\/pair\.html(\?debug)?#invite=[A-Za-z0-9_-]+$/);
    await shot(owner, 'owner-2-waiting');

    // ---- the phone opens it: nothing on the network until it agrees ---------------------
    const phone = await device({ width: 390, height: 844 });
    await phone.goto(link);
    await phase(phone, 'card');
    await phone.waitForTimeout(400);
    assert.equal(net.opened(phone), 0, 'the joining page connected before the person agreed');
    assert.match(await phone.textContent('#lede'), /home/);
    await shot(phone, 'phone-1-invitation');
    await phone.fill('#device-name', '我的手机');
    await phone.click('#agree');

    // ---- both screens show the same six digits ------------------------------------------
    await Promise.all([phase(owner, 'compare'), phase(phone, 'compare')]);
    const [a, b] = [await code(owner), await code(phone)];
    assert.match(a, /^\d{6}$/);
    assert.equal(a, b, 'the two screens show different codes');
    assert.equal(net.opened(phone), 1);
    assert.match(await owner.textContent('#who'), /我的手机/);
    await owner.waitForTimeout(700); // let the code finish unrolling
    await shot(owner, 'owner-3-compare');
    await shot(phone, 'phone-2-compare');

    // ---- the person confirms on the inviting device -------------------------------------
    await owner.click('#admit');
    await Promise.all([phase(owner, 'done'), phase(phone, 'done')]);
    const ownerDid = (await owner.textContent('#owner-did')).trim();
    const stored = await phone.evaluate(async (owners) => {
      const keys = await import('./client/keystore.js');
      const pairing = await import('./client/pairing.js');
      const [admission] = await keys.listMemberships();
      return { admission, checked: await pairing.verifyAdmission(admission, { owners, mesh: 'home' }) };
    }, [ownerDid]);
    assert.ok(stored.checked.ok, stored.checked.reason);
    assert.equal(stored.admission.name, '我的手机');
    assert.deepEqual(stored.admission.roles, ['forward']);
    await owner.waitForFunction(() => document.querySelectorAll('#devices li').length === 1);
    await shot(owner, 'owner-4-done');
    if (shotsAt) {
      await owner.click('.lang button[data-lang="en"]');
      await shot(owner, 'owner-5-done-en');
      await owner.click('.lang button[data-lang="zh"]');
    }
    await shot(phone, 'phone-3-done');
    console.log(`ok  paired: both screens showed ${a}; the phone holds a verified admission`);

    // ---- 不一样: nothing is admitted ----------------------------------------------------
    await owner.click('#again');
    await phase(owner, 'form');
    await owner.click('#write');
    await phase(owner, 'waiting');
    const second = await owner.inputValue('#link');
    const tablet = await device({ width: 820, height: 1180 });
    await tablet.goto(second);
    await phase(tablet, 'card');
    await tablet.click('#agree');
    await Promise.all([phase(owner, 'compare'), phase(tablet, 'compare')]);
    await tablet.click('#differ');
    await Promise.all([phase(owner, 'ended'), phase(tablet, 'ended')]);
    assert.match(await owner.textContent('#status'), /对方取消了/);
    assert.equal(await owner.$$eval('#devices li', (els) => els.length), 1);
    assert.equal(await tablet.evaluate(async () => (await (await import('./client/keystore.js')).listMemberships()).length), 0);
    console.log('ok  a mismatch reported on either screen admits nothing');

    // ---- an altered invitation is refused before anything connects ----------------------
    const token = second.split('#invite=')[1];
    const invite = JSON.parse(Buffer.from(token, 'base64url').toString('utf8'));
    const forged = Buffer.from(JSON.stringify({ ...invite, mesh: 'office' }), 'utf8').toString('base64url');
    const stranger = await device({ width: 390, height: 844 }, { locale: 'en-US', colorScheme: 'light' });
    await stranger.goto(`${origin}/pair.html#invite=${forged}`);
    await phase(stranger, 'invalid');
    assert.match(await stranger.textContent('#invalid-reason'), /signature doesn’t match/);
    assert.equal(await stranger.isVisible('#agree'), false);
    await stranger.waitForTimeout(300);
    assert.equal(net.opened(stranger), 0);
    await shot(stranger, 'stranger-invalid');
    console.log('ok  an altered invitation is refused offline, with no agree button');

    // ---- a plain-http LAN address: no keys in the browser, and the page says so -------
    const lan = await device({ width: 390, height: 844 });
    await lan.goto(`http://lan.test:${server.address().port}/pair.html#invite=${token}`);
    await phase(lan, 'invalid');
    assert.match(await lan.textContent('#invalid-reason'), /https:\/\/ 或 localhost/);
    assert.equal(net.opened(lan), 0);
    await shot(lan, 'lan-insecure');
    console.log('ok  on a plain-http LAN address the page explains it needs https or localhost');

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
