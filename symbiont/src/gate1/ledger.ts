/**
 * Open Socratic probes -- the TypeScript twin of `ProbeLedger` in
 * `dialogue/secdogie_dialogue/dialogue.py`, with the same rules:
 *
 *  - only an explicit `UserClarification` naming an open probe resolves it;
 *  - a probe past its deadline is expired, and an expired probe is a "no";
 *  - a late answer revives nothing, and there is no default answer.
 */

import { DialogueType, type DialoguePacket, dialoguePacket, randomHex } from '../gate2/wire.ts';

export const DEFAULT_PROBE_TTL_SECONDS = 300;

export type ProbeStatus = 'open' | 'answered' | 'expired' | 'unknown';

export interface Probe {
  readonly probeId: string;
  readonly question: string;
  readonly options: readonly string[];
  readonly gateFinding: string;
  readonly expiresAt: number;
}

export interface Resolution {
  readonly probeId: string;
  readonly answer: string;
  /** The DID that answered (the authenticated sender of the clarification). */
  readonly answeredBy: string;
  readonly answeredAt: number;
}

export class ProbeLedger {
  readonly #ttl: number;
  readonly #clock: () => number;
  readonly #newId: () => string;
  readonly #probes = new Map<string, Probe>();
  readonly #status = new Map<string, ProbeStatus>();

  constructor(opts: { ttlSeconds?: number; clock?: () => number; idFactory?: () => string } = {}) {
    this.#ttl = opts.ttlSeconds ?? DEFAULT_PROBE_TTL_SECONDS;
    this.#clock = opts.clock ?? (() => Date.now() / 1000);
    this.#newId = opts.idFactory ?? (() => randomHex(8));
  }

  /** Records a new probe; returns it and the `SocraticQuestion` to send. */
  open(question: string, opts: { options?: readonly string[]; gateFinding?: string; ttlSeconds?: number } = {}): {
    probe: Probe;
    packet: DialoguePacket;
  } {
    const probeId = this.#newId();
    if (this.#probes.has(probeId)) throw new Error(`probe id ${probeId} already used`);
    const probe: Probe = {
      probeId,
      question,
      options: [...(opts.options ?? [])],
      gateFinding: opts.gateFinding ?? '',
      expiresAt: this.#clock() + (opts.ttlSeconds ?? this.#ttl),
    };
    this.#probes.set(probeId, probe);
    this.#status.set(probeId, 'open');
    const packet = dialoguePacket({
      probe_id: probeId,
      dialogue_type: DialogueType.SocraticQuestion,
      content: question,
      suggested_options: probe.options,
      gate_finding: probe.gateFinding,
    });
    return { probe, packet };
  }

  /**
   * Resolves the probe `pkt` answers. Returns null -- and changes nothing --
   * unless it is a non-empty clarification of a probe still open and not past
   * its deadline.
   */
  resolve(pkt: DialoguePacket, answeredBy: string, now = this.#clock()): Resolution | null {
    if (pkt.dialogue_type !== DialogueType.UserClarification || !pkt.content.trim()) return null;
    const id = pkt.in_reply_to;
    if (this.#status.get(id) !== 'open') return null;
    const probe = this.#probes.get(id)!;
    if (now >= probe.expiresAt) {
      this.#status.set(id, 'expired');
      return null;
    }
    this.#status.set(id, 'answered');
    return { probeId: id, answer: pkt.content, answeredBy, answeredAt: now };
  }

  /** Expires every open probe past its deadline; returns their ids. */
  expire(now = this.#clock()): string[] {
    const gone: string[] = [];
    for (const [id, st] of this.#status) {
      if (st === 'open' && now >= this.#probes.get(id)!.expiresAt) {
        this.#status.set(id, 'expired');
        gone.push(id);
      }
    }
    return gone;
  }

  /** Fails a probe now (the asker withdrew it, or the operator surface is gone). */
  withdraw(probeId: string): void {
    if (this.#status.get(probeId) === 'open') this.#status.set(probeId, 'expired');
  }

  status(probeId: string): ProbeStatus {
    return this.#status.get(probeId) ?? 'unknown';
  }

  get(probeId: string): Probe | undefined {
    return this.#probes.get(probeId);
  }
}
