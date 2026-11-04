/**
 * The "lend this browser to the mesh" page (mesh.html). Consent is explicit
 * at every step and nothing runs in the background:
 *
 *  1. "Create identity" makes a non-extractable Ed25519 key held in this tab's
 *     memory only (the repository's red lines forbid browser storage, and a
 *     forwarder needs none): the page shows its DID for the volunteer to send
 *     to the operator. A new tab is a new DID, admitted afresh.
 *  2. The operator returns a join bundle carrying an admission for that DID.
 *  3. "Join" (a click: user activation is required) starts the node. A banner
 *     shows that this tab is forwarding, with live counters.
 *  4. "Stop" or closing the tab ends it; "Leave" also discards the key.
 *
 * Nothing runs at load: no key, no socket, no timer until a click.
 */

import { WebCryptoSigner } from '../core/ed25519.ts';
import { readBundle } from './bundle.ts';
import { BrowserNode, wallClock } from './node.ts';
import { BrowserRtc } from './rtc.ts';

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (el === null) throw new Error(`missing #${id}`);
  return el as T;
};

let signer: WebCryptoSigner | null = null;
let running: { node: BrowserNode; rtc: BrowserRtc; timer: ReturnType<typeof setInterval> } | null = null;

function log(line: string): void {
  const out = $('log');
  const row = document.createElement('div');
  row.textContent = `${new Date().toLocaleTimeString()}  ${line}`;
  out.prepend(row);
  while (out.childElementCount > 200) out.lastElementChild?.remove();
}

function render(): void {
  $('did').textContent = signer?.did ?? 'no identity yet';
  $<HTMLButtonElement>('create').disabled = signer !== null || running !== null;
  $<HTMLButtonElement>('join').disabled = signer === null || running !== null;
  $<HTMLButtonElement>('stop').disabled = running === null;
  $<HTMLButtonElement>('leave').disabled = signer === null;
  const banner = $('status');
  if (running === null) {
    banner.dataset['state'] = 'idle';
    banner.textContent = 'Not joined. This tab forwards nothing.';
    $('stats').textContent = '';
    return;
  }
  const s = running.node.stats();
  banner.dataset['state'] = s.mode === 'lockdown' ? 'lockdown' : s.neighbors.length ? 'joined' : 'connecting';
  banner.textContent = s.mode === 'lockdown'
    ? 'Joined, Lockdown: carrying Lockdown decisions only.'
    : s.neighbors.length ? 'Joined: this tab is forwarding for the mesh.' : 'Joining: dialing the entry nodes...';
  $('stats').textContent =
    `links ${s.neighbors.length} | carried ${s.carried} | refused ${s.refused} | flooded ${s.flooded} | dropped ${s.dropped}`;
  $('stats').dataset['links'] = s.neighbors.join(' ');
}

async function onCreate(): Promise<void> {
  signer = await WebCryptoSigner.generate();
  log('identity created; send this DID to your operator for an admission');
  render();
}

async function onJoin(): Promise<void> {
  // Explicit consent: only a click starts the node.
  if (navigator.userActivation !== undefined && !navigator.userActivation.isActive) {
    log('join must be started by a click');
    return;
  }
  if (signer === null || running !== null) return;
  let bundle;
  try {
    bundle = await readBundle($<HTMLTextAreaElement>('bundle').value, signer.did, wallClock());
  } catch (e) {
    log((e as Error).message);
    return;
  }
  const node = new BrowserNode(signer, bundle.trust, { config: { heartbeatEvery: bundle.heartbeatEvery } });
  const rtc = new BrowserRtc(signer, node, {
    signalingUrl: bundle.signalingUrl, salt: bundle.salt, iceServers: bundle.iceServers,
    admission: bundle.admission, log,
  });
  await node.publish(bundle.admission);
  for (const entry of bundle.entries) rtc.connect(entry);
  const timer = setInterval(() => {
    void node.tick().then(render);
  }, 250);
  running = { node, rtc, timer };
  log(`joining through ${bundle.entries.length} entry node(s)`);
  render();
}

function stop(): void {
  if (running === null) return;
  clearInterval(running.timer);
  running.rtc.close();
  running = null;
  log('stopped');
  render();
}

function onLeave(): void {
  stop();
  signer = null; // the only reference: the non-extractable key goes with it
  log('identity discarded');
  render();
}

function main(): void {
  $('create').addEventListener('click', () => void onCreate());
  $('join').addEventListener('click', () => void onJoin());
  $('stop').addEventListener('click', stop);
  $('leave').addEventListener('click', onLeave);
  window.addEventListener('pagehide', stop);
  render();
  document.body.dataset['ready'] = '1';
}

main();
