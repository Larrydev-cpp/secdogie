// Loads the Rust-WASM engine the way the tests need it. Build it first:
//   npm run build:wasm
import { existsSync, readFileSync } from 'node:fs';

import { GraphEngine } from '../src/graph/wasm.ts';

const WASM = new URL('../wasm/secdogie_graph.wasm', import.meta.url);

export async function loadEngine(): Promise<GraphEngine> {
  if (!existsSync(WASM)) throw new Error('wasm/secdogie_graph.wasm is missing: run `npm run build:wasm` first');
  return GraphEngine.fromBytes(readFileSync(WASM));
}

export const DOCS = 'https://docs.example.com';
export const FORUM = 'https://forum.example.com';

/** A small public docs site and a forum, as their markup would read. */
export const PAGES: Record<string, string> = {
  [`${DOCS}/`]: `<!doctype html><title>Docs</title>
    <a href="/guide/install">Install</a> <a href="/guide/start?lang=en">Start</a>
    <a href="/account/settings">Account</a> <a href="https://forum.example.com/">Forum</a>
    <form action="/search"><input name="q"></form>`,
  [`${DOCS}/account/settings`]: `<h1>Settings</h1>
    <form action="/account/delete" method="post">
      <input type="hidden" name="csrf" value="tok-123"><input name="confirm"><select name="reason"></select>
    </form>
    <form action="/login" method="post"><input name="user"><input type="password" name="pw"></form>`,
  [`${FORUM}/`]: `<a href="/t/welcome">Welcome</a> <a href="/account">Account</a>
    <form action="/account/delete" method="post"><input name="confirm"></form>`,
};
