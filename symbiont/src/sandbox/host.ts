/**
 * The page's handle on the sandbox worker. Starting it is the person's act
 * (the demo starts it from a click), it never takes focus, and `stop()`
 * terminates it. Works over any port with `postMessage` / message events, so
 * tests can stand a {@link SandboxCore} behind it without a real Worker.
 */

import type { SandboxResult } from './core.ts';

export interface PortLike {
  postMessage(msg: unknown, transfer?: Transferable[]): void;
  addEventListener(type: 'message', fn: (ev: { data: unknown }) => void): void;
  terminate?(): void;
}

export class SandboxHost {
  readonly #port: PortLike;
  readonly #timeoutMs: number;
  #next = 1;
  readonly #waiting = new Map<number, { resolve: (r: SandboxResult) => void; timer: ReturnType<typeof setTimeout> }>();
  #ready: Promise<readonly string[]>;

  constructor(port: PortLike, init: { policy: unknown; wasm: ArrayBuffer }, opts: { timeoutMs?: number } = {}) {
    this.#port = port;
    this.#timeoutMs = opts.timeoutMs ?? 45_000;
    this.#ready = new Promise((resolve, reject) => {
      port.addEventListener('message', (ev) => {
        const m = ev.data as { type: string; id?: number; result?: SandboxResult; error?: string; allowedOrigins?: string[] };
        if (m.type === 'ready') resolve(m.allowedOrigins ?? []);
        else if (m.type === 'error') reject(new Error(m.error));
        else if (m.type === 'result' && m.id !== undefined) {
          const w = this.#waiting.get(m.id);
          if (w) {
            clearTimeout(w.timer);
            this.#waiting.delete(m.id);
            w.resolve(m.result!);
          }
        }
      });
    });
    port.postMessage({ type: 'init', policy: init.policy, wasm: init.wasm });
  }

  /** Resolves with the allowlist the sandbox actually enforces. */
  ready(): Promise<readonly string[]> {
    return this.#ready;
  }

  async scan(url: string): Promise<SandboxResult> {
    await this.#ready;
    const id = this.#next++;
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.#waiting.delete(id);
        resolve({ kind: 'failed', url, reason: 'the sandbox did not answer in time' });
      }, this.#timeoutMs);
      this.#waiting.set(id, { resolve, timer });
      this.#port.postMessage({ type: 'scan', id, url });
    });
  }

  stop(): void {
    for (const [, w] of this.#waiting) clearTimeout(w.timer);
    this.#waiting.clear();
    this.#port.terminate?.();
  }
}
