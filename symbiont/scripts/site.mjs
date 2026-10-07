// Assembles the deployable operator page in site/: index.html, app.css, the
// compiled modules and the transport -- and nothing else (no tests, no
// sources, no WASM). The gateway is fixed here, at build time:
//
//   SECDOGIE_SIGNAL_URL=wss://<gateway>/ws  [SECDOGIE_ICE='["stun:stun.cloudflare.com:3478"]']  node scripts/site.mjs
//
// Without SECDOGIE_SIGNAL_URL it builds the local-development page (wrangler
// dev on 127.0.0.1:8787, no STUN). It writes site/_headers (Cloudflare Pages)
// with the CSP, anti-framing and isolation headers; scripts/serve.mjs sends
// the same headers locally.
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, 'site');
const LOCAL = new Set(['localhost', '127.0.0.1', '[::1]']);

const signal = process.env.SECDOGIE_SIGNAL_URL || 'ws://127.0.0.1:8787/ws';
const url = new URL(signal);
const dev = url.protocol === 'ws:';
if (!(url.protocol === 'wss:' || (dev && LOCAL.has(url.hostname)))) {
  console.error('SECDOGIE_SIGNAL_URL must be wss:// (ws:// only to this machine)');
  process.exit(2);
}
const ice = JSON.parse(process.env.SECDOGIE_ICE || '[]');
if (!Array.isArray(ice) || !ice.every((u) => typeof u === 'string' && /^(stun|turns?):/.test(u))) {
  console.error('SECDOGIE_ICE must be a JSON list of stun:/turn: URLs');
  process.exit(2);
}
if (!existsSync(join(root, 'dist', 'ui', 'main.js')) || !existsSync(join(root, 'dist', 'vendor', 'web_peer.js'))) {
  console.error('run npm run build first');
  process.exit(2);
}

// In development the page may reach a gateway on any local port (tests start one on a random port).
const connect = dev ? `'self' ws://127.0.0.1:* ws://localhost:*` : `'self' ${url.protocol}//${url.host}`;
const csp = [
  "default-src 'none'", "script-src 'self'", "style-src 'self'", `connect-src ${connect}`, "img-src 'self' data:",
  "object-src 'none'", "base-uri 'none'", "form-action 'none'",
].join('; ');

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
cpSync(join(root, 'dist'), join(out, 'dist'), { recursive: true, filter: (p) => !p.endsWith('.map') && !p.includes(`${join('dist', 'sandbox')}`) });
cpSync(join(root, 'app.css'), join(out, 'app.css'));
// the meta copy cannot carry frame-ancestors; the headers below do
const html = readFileSync(join(root, 'index.html'), 'utf8').replace(/(<meta http-equiv="Content-Security-Policy" content=")[^"]*(")/, `$1${csp}$2`);
writeFileSync(join(out, 'index.html'), html);
writeFileSync(join(out, 'dist', 'config.js'),
  `// written by scripts/site.mjs\nexport const CONFIG = ${JSON.stringify({ signalUrl: signal, iceServers: ice.map((u) => ({ urls: u })) })};\n`);
writeFileSync(join(out, '_headers'), [
  '/*',
  `  Content-Security-Policy: ${csp}; frame-ancestors 'none'`,
  '  X-Frame-Options: DENY',
  '  Cross-Origin-Opener-Policy: same-origin',
  '  Cross-Origin-Resource-Policy: same-origin',
  '  Referrer-Policy: no-referrer',
  '  X-Content-Type-Options: nosniff',
  '  Permissions-Policy: camera=(), microphone=(), geolocation=(), display-capture=(), usb=(), payment=()',
  '',
].join('\n'));
console.log(`site/ built for ${signal}${dev ? ' (local development)' : ''}`);
