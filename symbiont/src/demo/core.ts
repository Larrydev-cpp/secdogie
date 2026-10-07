/**
 * `#demo`: the page with an in-page node instead of your computer. Keys are
 * made fresh in memory for the visit and thrown away with it -- the demo never
 * opens the real key store, never touches the network, and the header always
 * says "demo", never "connected".
 */

import { type Signer, WebCryptoSigner } from '../core/ed25519.ts';
import type { OperatorKeyring } from '../gate2/signer_flow.ts';
import type { LinkPhase, PairingView, PhaseInfo } from '../net/attach.ts';
import type { Keys } from '../net/keystore.ts';
import { type FrameLink, OperatorClient, type OperatorClientOptions } from '../client/operator_client.ts';
import type { Dict } from '../voice/dict.ts';
import { DemoAgent } from './agent.ts';

export class DemoLink implements FrameLink {
  readonly phase: LinkPhase = 'demo';
  readonly info: PhaseInfo = { reason: null };
  readonly pairing: PairingView | null = null;
  readonly canApprove = true;
  readonly keys: Keys;
  readonly nodeDid: string;
  readonly agent: DemoAgent;
  readonly #frames = new Set<(d: Uint8Array<ArrayBuffer>) => void>();
  readonly #bound = new Set<(id: number) => void>();

  constructor(keys: Keys, node: WebCryptoSigner, lang: 'zh' | 'en', delay?: (ms: number) => Promise<void>) {
    this.keys = keys;
    this.nodeDid = node.did;
    this.agent = new DemoAgent({
      node, appDid: keys.app.did, operatorDid: keys.operator.did, lang,
      send: (raw) => queueMicrotask(() => {
        for (const cb of this.#frames) cb(raw);
      }),
      ...(delay ? { delay } : {}),
    });
  }

  onPhase(): () => void {
    return () => undefined;
  }

  onFrame(cb: (d: Uint8Array<ArrayBuffer>) => void): () => void {
    this.#frames.add(cb);
    return () => this.#frames.delete(cb);
  }

  onBound(cb: (id: number) => void): () => void {
    this.#bound.add(cb);
    return () => this.#bound.delete(cb);
  }

  async send(data: Uint8Array): Promise<boolean> {
    await this.agent.receive(data);
    return true;
  }

  async confirmPairing(): Promise<boolean> {
    return false;
  }

  cancelPairing(): void {}

  /** The demo node is "attached" from the start. */
  attach(): void {
    this.agent.start();
    for (const cb of this.#bound) cb(1);
  }
}

export interface DemoOptions {
  readonly keyringFor?: (operator: Signer) => OperatorKeyring;
  readonly activation?: () => boolean;
  readonly delay?: (ms: number) => Promise<void>;
  readonly timers?: OperatorClientOptions['timers'];
}

export async function demoCore(voice: Dict, opts: DemoOptions = {}): Promise<{ client: OperatorClient; link: DemoLink }> {
  const keys: Keys = { app: await WebCryptoSigner.generate(), operator: await WebCryptoSigner.generate() };
  const node = await WebCryptoSigner.generate();
  const link = new DemoLink(keys, node, voice.lang === 'en' ? 'en' : 'zh', opts.delay);
  const client = new OperatorClient({ link, voice, ...opts });
  link.attach();
  return { client, link };
}
