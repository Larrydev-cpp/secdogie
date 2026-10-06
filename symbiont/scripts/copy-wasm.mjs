// Copies the release build of graph/ (secdogie-graph, wasm32) next to this
// package, where both the tests and the browser loader look for it.
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, '..', '..', 'graph', 'target', 'wasm32-unknown-unknown', 'release', 'secdogie_graph.wasm');
const dst = join(here, '..', 'wasm', 'secdogie_graph.wasm');
mkdirSync(dirname(dst), { recursive: true });
copyFileSync(src, dst);
console.log(`copied ${src} -> ${dst}`);
