/**
 * The sandbox worker: a dedicated Web Worker with no DOM, no cookies and no
 * storage of its own, that fetches public pages inside the policy and returns
 * candidate states. Its policy and engine are fixed by the first `init`; a
 * second `init` is refused, so nothing can widen the boundary later.
 *
 *   -> {type: 'init', policy, wasm: ArrayBuffer}   <- {type: 'ready'} | {type: 'error'}
 *   -> {type: 'scan', id, url}                     <- {type: 'result', id, result}
 */

import { GraphEngine } from '../graph/wasm.ts';
import { SandboxCore } from './core.ts';
import { makePolicy } from './policy.ts';

declare const self: DedicatedWorkerGlobalScope;

let core: SandboxCore | null = null;
let starting = false;

self.addEventListener('message', (ev: MessageEvent) => {
  const msg = ev.data as { type?: string; id?: number; url?: string; policy?: unknown; wasm?: ArrayBuffer };
  if (msg.type === 'init') {
    if (core !== null || starting) {
      self.postMessage({ type: 'error', error: 'already initialized: the boundary cannot be changed' });
      return;
    }
    starting = true;
    void (async () => {
      try {
        const policy = makePolicy(msg.policy as Parameters<typeof makePolicy>[0]);
        const engine = await GraphEngine.fromBytes(msg.wasm!);
        core = new SandboxCore(policy, engine, (input, init) => fetch(input, init));
        self.postMessage({ type: 'ready', allowedOrigins: policy.allowedOrigins });
      } catch (e) {
        self.postMessage({ type: 'error', error: (e as Error).message });
      }
    })();
    return;
  }
  if (msg.type === 'scan' && typeof msg.id === 'number' && typeof msg.url === 'string') {
    const id = msg.id;
    if (core === null) {
      self.postMessage({ type: 'result', id, result: { kind: 'refused', url: msg.url, reason: 'sandbox not initialized' } });
      return;
    }
    void core.scan(msg.url).then(
      (result) => self.postMessage({ type: 'result', id, result }),
      (e: Error) => self.postMessage({ type: 'result', id, result: { kind: 'failed', url: msg.url, reason: e.message } }),
    );
  }
});
