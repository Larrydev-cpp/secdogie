/**
 * The operator page. On DOMContentLoaded it attaches to your computer by
 * itself (net/attach.ts) -- or, at `#demo`, to an in-page demo node -- and
 * draws one calm column: a status dot, the conversation, a floating composer.
 *
 *  - Enter sends, Shift+Enter starts a new line; while a question is open and
 *    the composer was empty when it appeared, what you type answers it.
 *  - While you type (and for 1.5 s after), or while the page is hidden, new
 *    questions and consent cards wait; they appear when you are free, and a
 *    consent card that reappears needs a fresh moment before Approve works.
 *  - "Stop" shows only while something is running.
 *  - Inside a frame the page attaches to nothing and offers nothing to click.
 *
 * It never calls focus(), never opens a dialog, and puts nothing mechanical on
 * screen; DevTools gets the details (core/trace.ts).
 */

import type { CoreLink } from '../client/core_link.ts';
import { OperatorClient } from '../client/operator_client.ts';
import { CONFIG } from '../config.ts';
import { trace } from '../core/trace.ts';
import { demoCore } from '../demo/core.ts';
import { Attachment, type BroadcastLike } from '../net/attach.ts';
import { Keystore, indexedDbStore } from '../net/keystore.ts';
import type { PeerApi } from '../net/peer_api.ts';
import type { Dict } from '../voice/dict.ts';
import { voiceFor } from '../voice/locale.ts';
import type { DocLike, El } from './dom.ts';
import { ambientLine, drawPresence } from './presence.ts';
import { ThreadView } from './render.ts';

const TYPING_PAUSE_MS = 1500;

const $ = <T extends HTMLElement>(id: string): T => {
  const e = document.getElementById(id);
  if (!e) throw new Error(`the page is missing #${id}`);
  return e as T;
};

function activation(): boolean {
  return navigator.userActivation?.isActive ?? false;
}

async function realCore(voice: Dict): Promise<CoreLink> {
  const url = new URL('../vendor/web_peer.js', import.meta.url).href;
  const peer = (await import(url)) as PeerApi;
  const tabs: BroadcastLike | null = 'BroadcastChannel' in globalThis ? (new BroadcastChannel('secdogie-symbiont') as unknown as BroadcastLike) : null;
  const attachment = new Attachment({
    peer,
    keystore: new Keystore(indexedDbStore()),
    config: CONFIG,
    hash: location.hash,
    clearHash: () => history.replaceState(null, '', location.pathname + location.search),
    tabs,
    activation,
    persist: () => void navigator.storage?.persist?.().catch(() => undefined),
  });
  const client = new OperatorClient({ link: attachment, voice, activation });
  void attachment.start();
  return client;
}

function mount(core: CoreLink, voice: Dict): void {
  const thread = $('thread');
  const input = $<HTMLTextAreaElement>('input');
  const send = $<HTMLButtonElement>('send');
  const stop = $<HTMLButtonElement>('stop');
  const ambient = $('ambient');
  const parts = { dot: $('dot') as unknown as El, label: $('presence-label') as unknown as El, hint: $('presence-hint') as unknown as El, group: $('presence') as unknown as El };

  send.textContent = voice.composer.send;
  stop.textContent = voice.composer.stop;

  let replyTo: string | null = null;
  let armTimer: ReturnType<typeof setTimeout> | null = null;
  const view = new ThreadView(document as unknown as DocLike, thread as unknown as El, voice, {
    answer: (id, value) => core.answer(id, value),
    approve: (id) => void core.approve(id),
    cancel: (id) => core.cancel(id),
    confirmPairing: () => void core.confirmPairing(),
    cancelPairing: () => core.cancelPairing(),
  });

  const draw = () => {
    const turns = core.turns();
    const p = core.presence;
    drawPresence(voice, p, parts);
    const line = ambientLine(voice, p, core.activity, turns.length === 0);
    ambient.textContent = line;
    ambient.hidden = line === '';
    // a new open question binds the composer only if it is empty right now
    const open = core.replyTo;
    if (open !== replyTo) {
      if (open === null || input.value.trim() === '') replyTo = open;
    }
    const online = p.phase === 'connected' || p.phase === 'demo';
    input.placeholder = !online ? voice.composer.offline : replyTo ? voice.composer.replyPlaceholder : voice.composer.placeholder;
    stop.hidden = !core.activity.running;
    const next = view.render(turns, core.now());
    if (armTimer) clearTimeout(armTimer);
    armTimer = next === null ? null : setTimeout(() => view.arm(core.now()), Math.max(0, (next - core.now()) * 1000) + 20);
  };
  core.onChange(draw);
  draw();

  const submit = () => {
    const text = input.value.trim();
    if (!text) return;
    const turn = replyTo && core.turns().find((t) => t.id === replyTo && t.kind === 'ask' && t.state === 'open');
    if (turn) core.answer(turn.id, text);
    else core.say(text);
    input.value = '';
    replyTo = null;
    holdUntilPause(false);
    draw();
  };
  send.addEventListener('click', submit);
  $('composer').addEventListener('submit', (ev) => {
    ev.preventDefault();
    submit();
  });
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault();
      submit();
    }
  });
  stop.addEventListener('click', () => core.stop());

  // -- attention: hold new cards while the person types or is away ----------------
  let pauseTimer: ReturnType<typeof setTimeout> | null = null;
  const holdUntilPause = (typing: boolean) => {
    if (pauseTimer) clearTimeout(pauseTimer);
    pauseTimer = null;
    if (typing && input.value.trim() !== '') {
      core.hold(true);
      pauseTimer = setTimeout(() => core.hold(document.visibilityState === 'hidden'), TYPING_PAUSE_MS);
    } else if (document.visibilityState !== 'hidden') {
      core.hold(false);
    }
  };
  input.addEventListener('input', () => holdUntilPause(true));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') core.hold(true);
    else {
      core.hold(false);
      core.rearm();
    }
  });
}

function framed(voice: Dict): void {
  const ambient = $('ambient');
  ambient.textContent = voice.ambient.framed;
  ambient.hidden = false;
  $('composer').setAttribute('hidden', '');
  $('presence-label').textContent = voice.presence.refused;
}

async function boot(): Promise<void> {
  const voice = voiceFor(navigator.languages ?? [navigator.language]);
  document.documentElement.lang = voice.lang;
  if (window.top !== window.self) {
    trace('the page is inside a frame: not attaching');
    framed(voice);
    return;
  }
  const demo = location.hash === '#demo';
  try {
    const core = demo ? (await demoCore(voice, { activation })).client : await realCore(voice);
    mount(core, voice);
  } catch (e) {
    trace('the page could not start', { error: (e as Error).message });
    const ambient = $('ambient');
    ambient.textContent = voice.presenceHint.unreachable;
    ambient.hidden = false;
  }
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => void boot());
else void boot();
