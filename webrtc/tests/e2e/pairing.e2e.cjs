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
//   * a link whose invitation was altered is refused, with no agree button.
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..', '..');
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json' };
const shotsAt = process.argv.includes('--shots') ? process.argv[process.argv.indexOf('--shots') + 1] : null;

function serve() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    const file = path.join(ROOT, path.normalize(decodeURIComponent(url.pathname)).replace(/^([/\\])+/, ''));
    if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { 'content-type': TYPES[path.extname(file)] ?? 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

/** The gateway's protocol, played by the test: rooms of two, welcome / peer-joined / relay with `from`. */
function relay() {
  const rooms = new Map();
  const opened = new Map(); // page -> sockets opened
  let n = 0;
  async function attach(page) {
    opened.set(page, 0);
    await page.routeWebSocket(/\/ws/, (ws) => {
      opened.set(page, opened.get(page) + 1);
      const room = new URL(ws.url()).searchParams.get('room');
      const peers = rooms.get(room) ?? new Map();
      rooms.set(room, peers);
      const id = `p${++n}`;
      const others = [...peers.keys()];
      peers.set(id, ws);
      ws.send(JSON.stringify({ type: 'welcome', peerId: id, peers: others }));
      for (const o of others) peers.get(o).send(JSON.stringify({ type: 'peer-joined', peerId: id }));
      ws.onMessage((raw) => {
        const m = JSON.parse(raw);
        if (m.type === 'ping') return;
        peers.get(m.to)?.send(JSON.stringify({ type: m.type, from: id, payload: m.payload }));
      });
      ws.onClose(() => {
        peers.delete(id);
        for (const o of peers.values()) o.send(JSON.stringify({ type: 'peer-left', peerId: id }));
      });
    });
  }
  return { attach, opened: (page) => opened.get(page) };
}

const phase = (page, want, timeout = 20_000) =>
  page.waitForFunction((w) => document.body.dataset.phase === w, want, { timeout });
const code = (page) => page.$eval('#code', (el) => el.textContent.replace(/\D/g, ''));

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
  const device = async (viewport, { locale = 'zh-CN', colorScheme = 'dark' } = {}) => {
    const context = await browser.newContext({ locale, viewport, colorScheme });
    const page = await context.newPage();
    page.on('pageerror', (err) => pageErrors.push(`${err.message}`));
    await net.attach(page);
    return page;
  };
  let failed = false;
  try {
    // ---- the inviting device writes an invitation --------------------------------------
    const owner = await device({ width: 1280, height: 900 });
    await owner.goto(`${origin}/pair.html?signal=${encodeURIComponent(origin.replace('http', 'ws'))}/ws`);
    await phase(owner, 'form');
    await shot(owner, 'owner-1-form');
    await owner.fill('#owner-name', '书房的电脑');
    await owner.click('#write');
    await phase(owner, 'waiting');
    const link = await owner.inputValue('#link');
    assert.match(link, /\/pair\.html#invite=[A-Za-z0-9_-]+$/);
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
    assert.deepEqual(pageErrors, [], 'a page threw');
  } catch (err) {
    failed = true;
    console.error(err);
  } finally {
    await browser.close();
    server.close();
  }
  process.exit(failed ? 1 : 0);
})();
