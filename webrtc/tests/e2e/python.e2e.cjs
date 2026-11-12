// A browser and a Python device (Studio's side, transport/secdogie_transport)
// pair over real WebRTC and form one mesh, through a real signaling gateway
// (signal_server.py), both ways round.
//
//   node tests/e2e/python.e2e.cjs <python>      (python with secdogie-transport[webrtc])
//
//   A  Studio invites a phone: the phone opens the link in pair.html and agrees;
//      both show the same six digits; Studio admits it and hands over the roster;
//      then both go online and the phone sees Studio, notes pass both ways.
//   B  A browser invites Studio: Python joins with the link; same codes; the
//      browser admits it; both go online and see each other.
'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const readline = require('node:readline');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');

const { phase, serve, watchConsole } = require('./harness.cjs');

const PYTHON = process.argv[2] || 'python3';
const REPO = path.resolve(__dirname, '..', '..', '..');

/** A child process speaking JSON lines; `next(kind)` waits for the next event of that kind. */
function child(args) {
  const proc = spawn(PYTHON, args, { cwd: REPO, stdio: ['pipe', 'pipe', 'pipe'] });
  const events = [];
  const waiters = [];
  let stderr = '';
  proc.stderr.on('data', (d) => { stderr += d; });
  readline.createInterface({ input: proc.stdout }).on('line', (text) => {
    let ev;
    try { ev = JSON.parse(text); } catch { return; }
    events.push(ev);
    for (const w of [...waiters]) if (w.test(ev)) { waiters.splice(waiters.indexOf(w), 1); w.resolve(ev); }
  });
  return {
    proc,
    send: (text) => proc.stdin.write(`${text}\n`),
    next: (test, timeout = 30_000) => new Promise((resolve, reject) => {
      const found = events.find(test);
      if (found) { events.splice(events.indexOf(found), 1); resolve(found); return; }
      const w = { test, resolve };
      waiters.push(w);
      setTimeout(() => reject(new Error(`timed out waiting for a Python event; stderr:\n${stderr.slice(-2000)}`)), timeout);
    }),
    kill: () => proc.kill(),
  };
}

const code = (page) => page.$eval('#code', (el) => el.textContent.replace(/\D/g, ''));
const onlineIn = (devices) => devices.filter((d) => d.state === 'online').map((d) => d.did);

(async () => {
  const server = await serve();
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  const pages = [];
  const device = async (name, viewport = { width: 390, height: 844 }) => {
    const context = await browser.newContext({ locale: 'zh-CN', viewport });
    const page = await context.newPage();
    watchConsole(page, name);
    pages.push(page);
    return page;
  };
  const kids = [];
  let failed = false;
  try {
    // ---- A: Studio (Python) invites a phone (browser) ----------------------------------
    const studio = child(['webrtc/tests/e2e/py_device.py', 'owner', `${origin}/pair.html?debug`]);
    kids.push(studio);
    const inv = await studio.next((e) => e.event === 'invite');
    const phone = await device('phone');
    await phone.goto(inv.link);
    await phase(phone, 'card');
    await phone.fill('#device-name', '我的手机');
    await phone.click('#agree');
    await phase(phone, 'compare');
    const shown = await studio.next((e) => e.event === 'code');
    assert.equal(await code(phone), shown.code, 'Studio and the phone show different codes');
    assert.equal(shown.name, '我的手机');
    studio.send('admit');
    await phase(phone, 'done');
    const { admission } = await studio.next((e) => e.event === 'admitted');
    const held = await phone.evaluate(async () => {
      const k = await import('./client/keystore.js');
      return { memberships: await k.listMemberships(), roster: await k.rosterOf('home') };
    });
    assert.equal(held.memberships[0].device, admission.device);
    assert.equal(held.roster.admissions[0].device, inv.node, 'the phone got the roster with Studio in it');
    console.log(`ok  Studio invited a phone: both showed ${shown.code}; the phone holds its admission and the roster`);

    await phone.goto(`${origin}/mesh.html?debug&signal=${encodeURIComponent(inv.signal)}`);
    await phase(phone, 'offline');
    await phone.click('#online');
    await phone.waitForFunction((did) => document.querySelector(`#devices li[data-did="${did}"]`)?.dataset.state === 'online',
      inv.node, { timeout: 40_000 });
    assert.match(await phone.textContent(`#devices li[data-did="${inv.node}"]`), /书房的电脑/);
    await studio.next((e) => e.event === 'mesh' && onlineIn(e.devices).includes(admission.device), 40_000);
    studio.send('say 你好，手机');
    await phone.waitForFunction(() => /你好，手机/.test(document.querySelector('#notes').textContent), null, { timeout: 15_000 });
    await phone.fill('#say', '手机回话');
    await phone.click('#send');
    const note = await studio.next((e) => e.event === 'note');
    assert.equal(note.text, '手机回话');
    console.log('ok  Studio and the phone online in one mesh; notes pass both ways');
    studio.send('quit');

    // ---- B: a browser invites Studio (Python) -----------------------------------------
    const gateway = child(['-m', 'secdogie_transport.signal_server']);
    kids.push(gateway);
    const { url } = await gateway.next((e) => typeof e.url === 'string');
    const owner = await device('owner', { width: 1280, height: 900 });
    await owner.goto(`${origin}/pair.html?debug&signal=${encodeURIComponent(url)}`);
    await phase(owner, 'form');
    await owner.click('#write');
    await phase(owner, 'waiting');
    const joiner = child(['webrtc/tests/e2e/py_device.py', 'join']);
    kids.push(joiner);
    joiner.send(JSON.stringify({ link: await owner.inputValue('#link') }));
    await phase(owner, 'compare');
    const theirs = await joiner.next((e) => e.event === 'code');
    assert.equal(await code(owner), theirs.code);
    assert.match(await owner.textContent('#who'), /书房的电脑/);
    await owner.click('#admit');
    const joined = await joiner.next((e) => e.event === 'admitted');
    const ownerDid = (await owner.textContent('#owner-did')).trim();
    assert.equal(joined.admission.signer, ownerDid);
    console.log(`ok  a browser invited Studio: both showed ${theirs.code}; Studio holds the admission`);

    await owner.goto(`${origin}/mesh.html?debug&signal=${encodeURIComponent(url)}`);
    await phase(owner, 'offline');
    await owner.click('#online');
    await owner.waitForFunction((did) => document.querySelector(`#devices li[data-did="${did}"]`)?.dataset.state === 'online',
      joined.admission.device, { timeout: 40_000 });
    await joiner.next((e) => e.event === 'mesh' && onlineIn(e.devices).includes(ownerDid), 40_000);
    console.log('ok  the browser and Studio online in one mesh');
    joiner.send('quit');
  } catch (err) {
    failed = true;
    console.error(err);
    for (const p of pages) if (!p.isClosed()) console.error(await p.diagnose());
  } finally {
    for (const k of kids) k.kill();
    await browser.close();
    server.close();
  }
  process.exit(failed ? 1 : 0);
})();
