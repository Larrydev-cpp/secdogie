// What the browser end-to-end tests share: a static server for webrtc/, the
// signaling gateway's protocol played by the test (Playwright WebSocket
// routing, so nothing leaves this machine), and small helpers.
'use strict';

const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..', '..');
const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
};

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

/** The gateway's protocol: rooms of two, welcome / peer-joined / peer-left, relay with `from`. */
function relay() {
  const rooms = new Map();
  const opened = new Map(); // page -> sockets it opened
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
      if (others.length >= 2) {
        ws.send(JSON.stringify({ type: 'error', code: 'room-full', message: 'room is full' }));
        ws.close({ code: 4001 });
        return;
      }
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

/** Keep each page's last console lines (the pages log their WebRTC steps with ?debug). */
function watchConsole(page, name, keep = 60) {
  const lines = [];
  page.on('console', (msg) => {
    lines.push(`${new Date().toISOString().slice(11, 23)} ${msg.text()}`);
    if (lines.length > keep) lines.shift();
  });
  page.diagnose = async () => {
    let view = {};
    try {
      view = await page.evaluate(() => ({
        url: location.pathname,
        phase: document.body.dataset.phase,
        status: document.getElementById('status')?.textContent ?? '',
        devices: [...document.querySelectorAll('#devices li')].map((li) => `${li.dataset.did?.slice(-6)}=${li.dataset.state}`),
      }));
    } catch (err) {
      view = { error: err.message };
    }
    return `--- ${name}: ${JSON.stringify(view)}\n${lines.join('\n')}`;
  };
}

const phase = (page, want, timeout = 20_000) =>
  page.waitForFunction((w) => document.body.dataset.phase === w, want, { timeout });

/** Pair `devicePage` into the mesh `ownerPage` holds, through pair.html, start to finish. */
async function pair(ownerPage, devicePage, { origin, name, ownerName = '' }) {
  const signal = encodeURIComponent(`${origin.replace('http', 'ws')}/ws`);
  await ownerPage.goto(`${origin}/pair.html?debug&signal=${signal}`);
  await phase(ownerPage, 'form');
  if (ownerName) await ownerPage.fill('#owner-name', ownerName);
  await ownerPage.click('#write');
  await phase(ownerPage, 'waiting');
  const link = await ownerPage.inputValue('#link');
  await devicePage.goto(link);
  await phase(devicePage, 'card');
  await devicePage.fill('#device-name', name);
  await devicePage.click('#agree');
  await Promise.all([phase(ownerPage, 'compare'), phase(devicePage, 'compare')]);
  await ownerPage.click('#admit');
  await Promise.all([phase(ownerPage, 'done'), phase(devicePage, 'done')]);
}

module.exports = { ROOT, pair, phase, relay, serve, watchConsole };
