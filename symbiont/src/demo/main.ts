/**
 * The offline demo: the whole loop in one page, with nothing leaving it until
 * you ask the sandbox to fetch an origin you typed in.
 *
 * Focus signals here are *simulated* by the controls (a page cannot see your
 * typing elsewhere, and SecDogie does not try). Keys are generated in this
 * tab with WebCrypto and never leave it: one for the agent, one for you as
 * the operator, whose key opens only on your own click.
 */

import { ProposalQueue } from '../attention/queue.ts';
import { AttentionScheduler } from '../attention/scheduler.ts';
import { type ContextClass, ManualFocusProvider } from '../attention/signals.ts';
import { sha256Hex } from '../core/canon.ts';
import { WebCryptoSigner } from '../core/ed25519.ts';
import { TrustSet } from '../core/envelope.ts';
import { Gate2Issuer } from '../gate2/issuer.ts';
import { UserGestureKeyring } from '../gate2/signer_flow.ts';
import { DualGatePipeline } from '../gates/pipeline.ts';
import { TopologyEngine } from '../graph/topology.ts';
import { GraphEngine, type StateGraph } from '../graph/wasm.ts';
import { SandboxHost } from '../sandbox/host.ts';
import { type DocLike, type El, renderStream } from '../stream/render.ts';
import { ConsciousnessStream } from '../stream/stream.ts';
import { zhReason } from '../stream/strings.ts';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

/** Sample pages so the topology is not empty before you fetch anything. */
const SAMPLE: Record<string, string> = {
  'https://docs.example.com/': `<a href="/guide/install">Install</a><a href="/guide/start?lang=en">Start</a>
    <a href="/account/settings">Account</a><form action="/search"><input name="q"></form>`,
  'https://docs.example.com/account/settings': `<form action="/account/delete" method="post">
    <input name="confirm"><select name="reason"></select></form>`,
  'https://forum.example.com/': `<a href="/t/welcome">Welcome</a>
    <form action="/account/delete" method="post"><input name="confirm"></form>`,
};

async function main(): Promise<void> {
  const wasmUrl = new URL('../../wasm/secdogie_graph.wasm', import.meta.url);
  const wasmBytes = await (await fetch(wasmUrl)).arrayBuffer();
  const engine = await GraphEngine.fromBytes(wasmBytes);
  const agent = await WebCryptoSigner.generate();
  const operator = await WebCryptoSigner.generate();
  const now = () => Date.now();

  const topo = new TopologyEngine({ graph: engine.createGraph([agent.did]), agent, clock: now });
  const queue = new ProposalQueue();
  const scheduler = new AttentionScheduler({ queue, clock: now });
  const stream = new ConsciousnessStream({ clock: now });
  const pipeline = new DualGatePipeline({
    stream,
    scheduler,
    topology: () => topo.snapshot(),
    planner: topo.graph,
    issuer: new Gate2Issuer({ agent, operators: new TrustSet([operator.did]) }),
    keyring: new UserGestureKeyring(operator),
    clock: now,
  });

  // ---- the swarm: a second in-page node to reconcile with ------------------------------
  let peer: StateGraph | null = null;
  let lastSyncAt: number | null = null;

  // ---- rendering ---------------------------------------------------------------------
  const root = $('stream');
  const handlers = {
    answer: (id: string, text: string) => void pipeline.answer(id, text),
    approve: (id: string) => void pipeline.approve(id),
    deny: (id: string) => void pipeline.deny(id),
  };
  const render = () => {
    // Keep what the person is typing in an open answer box across re-renders.
    const drafts = new Map<string, string>();
    root.querySelectorAll<HTMLInputElement>('[data-probe] input').forEach((i) => {
      const id = i.closest('[data-probe]')?.getAttribute('data-probe');
      if (id && i.value) drafts.set(id, i.value);
    });
    renderStream(document as unknown as DocLike, root as unknown as El, stream.items(), handlers, pipeline.nowSeconds());
    for (const [id, v] of drafts) {
      const i = root.querySelector<HTMLInputElement>(`[data-probe="${CSS.escape(id)}"] input`);
      if (i) i.value = v;
    }
    // Re-render when the next bubble becomes approvable.
    const waits = stream
      .items()
      .flatMap((it) => (it.kind === 'gate2' && it.bubble?.state === 'awaiting' ? [it.bubble.armedAt] : []))
      .map((at) => at * 1000 - now())
      .filter((ms) => ms > 0);
    if (waits.length) setTimeout(render, Math.min(...waits) + 20);
  };
  stream.onChange(render);

  let ambientKey = '';
  const refreshAmbient = () => {
    const f = topo.graph.frontier();
    const swarm = { peers: peer ? 1 : 0, heads: f.heads.length, deltas: f.count, lastSyncAt };
    const status = topo.status();
    const key = JSON.stringify([swarm, status, scheduler.budget.mode, lastSyncAt && Math.floor((now() - lastSyncAt) / 10_000)]);
    if (key !== ambientKey) {
      ambientKey = key;
      stream.setAmbient(swarm, status, scheduler.budget.mode);
    }
    $('mode').textContent = `注意力：${scheduler.budget.mode} · ${scheduler.budget.reasons.join('，')}`;
  };

  // ---- seed the topology offline -------------------------------------------------------
  const allowed = ['https://docs.example.com', 'https://forum.example.com'];
  for (const [url, html] of Object.entries(SAMPLE)) {
    const scan = engine.scanRoutes({ document_url: url, allowed_origins: allowed }, html);
    const { newStates } = await topo.record({
      scan,
      contentHash: await sha256Hex(html),
      byteLen: new TextEncoder().encode(html).length,
    });
    stream.narrate({ type: 'mapped', origin: new URL(url).origin, states: newStates, at: now() });
  }

  // ---- simulated focus -----------------------------------------------------------------
  const provider = new ManualFocusProvider();
  scheduler.attach(provider);
  let idleSince = now();
  setInterval(() => {
    const cpm = Number($<HTMLInputElement>('cpm').value);
    const context = $<HTMLSelectElement>('context').value as ContextClass;
    if (cpm > 0) idleSince = now();
    $('cpm-out').textContent = `${cpm} 次/分`;
    provider.push({ at: now(), surface: 'other', context, inputEvents: cpm / 60, sampleMs: 1000, idleMs: now() - idleSince });
    refreshAmbient();
  }, 1000);

  // ---- intents ---------------------------------------------------------------------------
  $('composer').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const input = $<HTMLInputElement>('intent');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    void pipeline.submit(text);
  });
  for (const b of document.querySelectorAll<HTMLButtonElement>('[data-intent]')) {
    b.addEventListener('click', () => void pipeline.submit(b.dataset['intent']!));
  }

  // ---- the swarm: reconcile with a second node -------------------------------------------
  $('sync').addEventListener('click', async () => {
    if (!peer) {
      peer = engine.createGraph([agent.did]);
      // the peer learned one page the local node has not
      const side = new TopologyEngine({ graph: peer, agent, clock: now });
      const html = '<a href="/pricing">Pricing</a><a href="/changelog">Changelog</a>';
      const scan = engine.scanRoutes({ document_url: 'https://docs.example.com/about', allowed_origins: allowed }, html);
      await side.record({ scan, contentHash: await sha256Hex(html), byteLen: html.length });
    }
    let moved = 0;
    for (const [dst, src] of [[topo.graph, peer], [peer, topo.graph]] as const) {
      const want = dst.onHave(src.have().heads);
      if (want) moved += dst.onDeltas(src.onWant(want.cids, want.have_heads).envelopes).report.inserted.length;
    }
    lastSyncAt = now();
    stream.narrate({ type: 'synced', peer: 'in-page-peer', deltas: moved, at: now() });
    refreshAmbient();
  });

  // ---- the sandbox (side pane, started by you) -------------------------------------------
  let host: SandboxHost | null = null;
  $('sandbox-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const out = $('sandbox-out');
    const url = $<HTMLInputElement>('sandbox-url').value.trim();
    try {
      if (host === null) {
        const origin = $<HTMLInputElement>('sandbox-origin').value.trim();
        const worker = new Worker(new URL('../sandbox/worker.js', import.meta.url), { type: 'module', name: 'secdogie-sandbox' });
        host = new SandboxHost(worker, { policy: { allowedOrigins: [origin] }, wasm: wasmBytes.slice(0) });
        out.textContent = `沙箱已启动，只放行：${(await host.ready()).join('、')}`;
        $<HTMLInputElement>('sandbox-origin').setAttribute('readonly', '');
      }
      const r = await host.scan(url);
      if (r.kind === 'scanned') {
        const { newStates } = await topo.record({ scan: r.scan, contentHash: r.contentHash, byteLen: r.byteLen });
        stream.narrate({ type: 'mapped', origin: new URL(r.url).origin, states: newStates, at: now() });
        out.textContent = `读到 ${r.byteLen} 字节${r.truncated ? '（已截断）' : ''}，${r.scan.candidates.length} 个候选状态，${r.scan.external} 个站外链接只计数不跟随。`;
      } else if (r.kind === 'redirect') {
        out.textContent = `对方要求重定向（${r.status}），没有跟随。${r.location ? `目标在白名单内：${r.location}，可再手动抓取。` : '目标不可见或不在白名单内。'}`;
      } else {
        stream.narrate({ type: 'fetch-refused', origin: safeOrigin(url), reason: r.reason, at: now() });
        out.textContent = `没有抓取：${zhReason(r.reason)}`;
      }
    } catch (e) {
      out.textContent = `沙箱没有启动：${(e as Error).message}`;
      host?.stop();
      host = null;
    }
    refreshAmbient();
  });
  $('sandbox-stop').addEventListener('click', () => {
    host?.stop();
    host = null;
    $<HTMLInputElement>('sandbox-origin').removeAttribute('readonly');
    $('sandbox-out').textContent = '沙箱已停止。';
  });

  refreshAmbient();
  render();
}

function safeOrigin(u: string): string {
  try {
    return new URL(u).origin;
  } catch {
    return u;
  }
}

main().catch((e: Error) => {
  $('stream').textContent = `启动失败：${e.message}`;
});
