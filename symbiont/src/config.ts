/**
 * Where this page reaches the node's signaling gateway, fixed when the page is
 * built (scripts/site.mjs rewrites this file in the built site from
 * SECDOGIE_SIGNAL_URL / SECDOGIE_ICE). A pairing link can never change it.
 * The defaults are for local development: `wrangler dev` on this machine and
 * no STUN at all.
 */

import type { IceServer } from './net/peer_api.ts';

export const CONFIG: { readonly signalUrl: string; readonly iceServers: readonly IceServer[] } = {
  signalUrl: 'ws://127.0.0.1:8787/ws',
  iceServers: [],
};
