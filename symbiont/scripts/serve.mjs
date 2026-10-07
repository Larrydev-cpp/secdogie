// A local server for site/ that sends exactly the headers site/_headers
// declares (the CSP with frame-ancestors, X-Frame-Options, COOP, ...), on
// 127.0.0.1 only. SecDogie's own port by default, so the page does not share
// an origin with whatever else runs on localhost:8080.
//
//   node scripts/serve.mjs [--port 8770] [--root site]
import { createServer } from 'node:http';
import { readFileSync, statSync } from 'node:fs';
import { extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
};
const root = resolve(fileURLToPath(new URL('..', import.meta.url)), opt('--root', 'site'));
const port = Number(opt('--port', '8770'));

const headers = {};
for (const line of readFileSync(join(root, '_headers'), 'utf8').split('\n')) {
  const m = /^\s+([A-Za-z-]+):\s*(.*)$/.exec(line);
  if (m) headers[m[1]] = m[2];
}
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json' };

const server = createServer((req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const file = normalize(join(root, path.endsWith('/') ? `${path}index.html` : path));
  if (!file.startsWith(root) || file.includes(`${root}/_headers`)) {
    res.writeHead(404).end();
    return;
  }
  try {
    if (!statSync(file).isFile()) throw new Error('not a file');
    res.writeHead(200, { ...headers, 'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream', 'Cache-Control': 'no-store' });
    res.end(readFileSync(file));
  } catch {
    res.writeHead(404, headers).end('not found\n');
  }
});
server.listen(port, '127.0.0.1', () => console.log(`serving ${root} on http://127.0.0.1:${server.address().port}/`));
