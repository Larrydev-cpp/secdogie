// The sandbox's fetch boundary: https only, an explicit allowlist, no
// credentials, bounded body and time, redirects reported but never followed.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { type FetchLike, boundedFetch } from '../src/sandbox/bounded_fetch.ts';
import { SandboxCore } from '../src/sandbox/core.ts';
import { SandboxHost } from '../src/sandbox/host.ts';
import { PolicyError, makePolicy } from '../src/sandbox/policy.ts';
import { DOCS, PAGES, loadEngine } from './engine.ts';

const html = (body: string, status = 200, headers: Record<string, string> = {}) =>
  new Response(body, { status, headers: { 'content-type': 'text/html; charset=utf-8', ...headers } });

function recording(respond: (url: string) => Response | Promise<Response>) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const f: FetchLike = async (url, init) => {
    calls.push({ url, init });
    return respond(url);
  };
  return { f, calls };
}

test('the policy is explicit and fixed', () => {
  assert.throws(() => makePolicy({ allowedOrigins: [] }), PolicyError);
  assert.throws(() => makePolicy({ allowedOrigins: ['http://docs.example.com'] }), /https/);
  assert.throws(() => makePolicy({ allowedOrigins: ['https://docs.example.com/'] }), /exactly/);
  assert.throws(() => makePolicy({ allowedOrigins: ['https://u:p@docs.example.com'] }), PolicyError);
  assert.throws(() => makePolicy({ allowedOrigins: [DOCS], maxBodyBytes: 1 << 30 }), /maxBodyBytes/);
  assert.throws(() => makePolicy({ allowedOrigins: [DOCS], timeoutMs: 120_000 }), /timeoutMs/);
  const p = makePolicy({ allowedOrigins: [DOCS] });
  assert.ok(Object.isFrozen(p) && Object.isFrozen(p.allowedOrigins));
});

test('every request omits credentials and refuses to follow redirects', async () => {
  const policy = makePolicy({ allowedOrigins: [DOCS] });
  const { f, calls } = recording(() => html('<a href="/x">x</a>'));
  const out = await boundedFetch(policy, `${DOCS}/page#frag`, f);
  assert.equal(out.kind, 'page');
  const { url, init } = calls[0]!;
  assert.equal(url, `${DOCS}/page`, 'the fragment never goes on the wire');
  assert.equal(init.credentials, 'omit');
  assert.equal(init.redirect, 'manual');
  assert.equal(init.referrerPolicy, 'no-referrer');
  assert.equal(init.method, 'GET');
  assert.deepEqual(Object.keys(init.headers as Record<string, string>), ['Accept']);
});

test('refused before any network: http, other origins, credentials in the URL', async () => {
  const policy = makePolicy({ allowedOrigins: [DOCS] });
  const { f, calls } = recording(() => html(''));
  for (const [u, why] of [
    ['http://docs.example.com/', 'not https'],
    ['https://evil.example/', 'origin not on the allowlist'],
    ['https://user:pw@docs.example.com/', 'credentials in the URL'],
    ['javascript:alert(1)', 'not https'],
  ] as const) {
    const out = await boundedFetch(policy, u, f);
    assert.deepEqual(out, { kind: 'refused', url: u, reason: why });
  }
  assert.equal(calls.length, 0);
});

test('a redirect is reported with its target only if that target is allowed', async () => {
  const policy = makePolicy({ allowedOrigins: [DOCS] });
  const inside = recording(() => new Response(null, { status: 302, headers: { location: '/moved' } }));
  const a = await boundedFetch(policy, `${DOCS}/old`, inside.f);
  assert.deepEqual(a, { kind: 'redirect', url: `${DOCS}/old`, status: 302, location: `${DOCS}/moved` });
  assert.equal(inside.calls.length, 1, 'not followed');
  const outside = recording(() => new Response(null, { status: 301, headers: { location: 'https://evil.example/' } }));
  const b = await boundedFetch(policy, `${DOCS}/old`, outside.f);
  assert.ok(b.kind === 'redirect' && b.location === null);
});

test('the body is capped and the time is bounded', async () => {
  const policy = makePolicy({ allowedOrigins: [DOCS], maxBodyBytes: 1000, timeoutMs: 50 });
  const big = recording(() => html('x'.repeat(5000)));
  const out = await boundedFetch(policy, `${DOCS}/`, big.f);
  assert.ok(out.kind === 'page' && out.truncated && out.byteLen === 1000);

  const hang: FetchLike = (_u, init) =>
    new Promise((_res, rej) => init.signal!.addEventListener('abort', () => rej(init.signal!.reason)));
  const slow = await boundedFetch(policy, `${DOCS}/`, hang);
  assert.deepEqual(slow, { kind: 'failed', url: `${DOCS}/`, reason: 'timed out' });

  const pdf = recording(() => new Response('%PDF', { headers: { 'content-type': 'application/pdf' } }));
  const notHtml = await boundedFetch(policy, `${DOCS}/`, pdf.f);
  assert.ok(notHtml.kind === 'failed' && /not HTML/.test(notHtml.reason));
  const cors: FetchLike = async () => {
    throw new TypeError('Failed to fetch');
  };
  const blocked = await boundedFetch(policy, `${DOCS}/`, cors);
  assert.ok(blocked.kind === 'failed' && /CORS/.test(blocked.reason));
});

test('the sandbox core returns candidate states, never the page', async () => {
  const engine = await loadEngine();
  const policy = makePolicy({ allowedOrigins: [DOCS] });
  const { f } = recording((url) => html(PAGES[url] ?? ''));
  const core = new SandboxCore(policy, engine, f);
  const r = await core.scan(`${DOCS}/account/settings`);
  assert.equal(r.kind, 'scanned');
  assert.ok(r.kind === 'scanned' && r.scan.candidates.length === 1 && /^[0-9a-f]{64}$/.test(r.contentHash));
  assert.ok(!('body' in r));

  // the same core behind the host protocol, as the worker runs it
  const listeners: Array<(ev: { data: unknown }) => void> = [];
  const port = {
    postMessage(msg: unknown) {
      const m = msg as { type: string; id?: number; url?: string };
      const reply = (data: unknown) => queueMicrotask(() => listeners.forEach((l) => l({ data })));
      if (m.type === 'init') reply({ type: 'ready', allowedOrigins: policy.allowedOrigins });
      else if (m.type === 'scan') void core.scan(m.url!).then((result) => reply({ type: 'result', id: m.id, result }));
    },
    addEventListener(_t: 'message', fn: (ev: { data: unknown }) => void) {
      listeners.push(fn);
    },
  };
  const host = new SandboxHost(port, { policy: { allowedOrigins: [DOCS] }, wasm: new ArrayBuffer(0) });
  assert.deepEqual(await host.ready(), [DOCS]);
  const viaHost = await host.scan(`${DOCS}/`);
  assert.equal(viaHost.kind, 'scanned');
  host.stop();
});
