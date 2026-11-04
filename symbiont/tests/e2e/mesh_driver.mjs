// Drives mesh.html in headless Chromium for inference/tests/test_browser_node.py,
// which runs the Python half of the mesh. One JSON object per line:
//
//   stdout {"did": "..."}                 after clicking "Create identity"
//   stdin  {"bundle": "..."}              paste the join bundle and click "Join"
//   stdout {"joined": ["did", ...]}       once linked to every entry node
//   stdin  {"stats": true}  -> stdout {"stats": {...}}
//   stdin  {"close": true}  -> close the tab -> stdout {"closed": true}
//
//   node tests/e2e/mesh_driver.mjs http://127.0.0.1:PORT/mesh.html
import { createRequire } from 'node:module';
import { createInterface } from 'node:readline';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright'); // the pre-installed one (NODE_PATH)

const out = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');
const lines = createInterface({ input: process.stdin })[Symbol.asyncIterator]();
const next = async () => {
  const { value, done } = await lines.next();
  if (done) throw new Error('stdin closed');
  return JSON.parse(value);
};

// Host candidates as real addresses: aiortc does not resolve Chromium's mDNS names.
const browser = await chromium.launch({ args: ['--disable-features=WebRtcHideLocalIpsWithMdns'] });
try {
  const page = await browser.newPage();
  page.on('console', (m) => process.stderr.write(`[page] ${m.text()}\n`));
  page.on('pageerror', (e) => process.stderr.write(`[page error] ${e.message}\n`));
  await page.goto(process.argv[2]);
  await page.waitForSelector('body[data-ready="1"]');
  await page.click('#create');
  await page.waitForFunction(() => document.getElementById('did').textContent.startsWith('did:key:'));
  out({ did: (await page.textContent('#did')).trim() });

  const { bundle } = await next();
  const entries = JSON.parse(bundle).entries.length;
  await page.fill('#bundle', bundle);
  await page.click('#join');
  await page.waitForFunction(
    (n) => (document.getElementById('stats').dataset.links || '').split(' ').filter(Boolean).length >= n,
    entries,
    { timeout: 90_000, polling: 200 },
  );
  out({ joined: (await page.getAttribute('#stats', 'data-links')).split(' ') });

  for (;;) {
    const cmd = await next();
    if (cmd.stats) {
      const text = await page.textContent('#stats');
      const stats = Object.fromEntries([...text.matchAll(/(\w+) (\d+)/g)].map((m) => [m[1], Number(m[2])]));
      out({ stats: { ...stats, status: await page.getAttribute('#status', 'data-state') } });
    } else if (cmd.close) {
      await page.close();
      out({ closed: true });
      break;
    }
  }
} finally {
  await browser.close();
}
