// Ships the WebRTC transport (webrtc/client/web_peer.js, plain JS) beside the
// compiled page, where ui/main.ts imports it from: dist/vendor/web_peer.js.
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, '..', '..', 'webrtc', 'client', 'web_peer.js');
const dst = join(here, '..', 'dist', 'vendor', 'web_peer.js');
mkdirSync(dirname(dst), { recursive: true });
copyFileSync(src, dst);
console.log(`copied ${src} -> ${dst}`);
