/**
 * secdogie WebRTC peer — the browser side of the signaling gateway in
 * ../signaling/worker.js.
 *
 * Nothing happens at import time: no socket, no RTCPeerConnection, no timer.
 * The only way in is startPeerConnection(), which refuses to run outside a user
 * gesture (a click), and closePeerConnection() tears everything down again.
 *
 *   startPeerConnection({signalingUrl, room, iceServers?, log?}) -> Promise<{peerId, room}>
 *   closePeerConnection()
 *   sendData(payload) -> Promise<void>
 *   onMessage(callback) -> unsubscribe        callback(data: string | ArrayBuffer)
 *   onStateChange(callback) -> unsubscribe    callback(state: PeerState, detail: string)
 *   currentDescriptions() -> {local, remote} | null
 *
 * Those drive one peer per page. createPeerLink() makes further, independent
 * peers with the same methods (start / close / send / onMessage /
 * onStateChange / currentDescriptions), for a page that keeps several links
 * at once: one per pair of your own devices (mesh.js).
 *
 * A room pairs two peers. The one that joins second makes the offer; the other
 * answers. Only the offerer ever re-offers after a lost link, so the two sides
 * never offer at each other at once, and every incoming offer starts a fresh
 * RTCPeerConnection.
 */

export const PeerState = Object.freeze({
  IDLE: 'idle', // not started, or closed by the user
  SIGNALING: 'signaling', // opening the signaling socket
  WAITING: 'waiting', // in the room, no P2P link yet
  NEGOTIATING: 'negotiating', // offer / answer / ICE in progress
  CONNECTED: 'connected', // data channel open: sendData() works
  DISCONNECTED: 'disconnected', // ICE lost connectivity; waiting for it to recover
  FAILED: 'failed', // gave up; startPeerConnection() may be called again
});

const DEFAULT_ICE_SERVERS = [{ urls: 'stun:stun.l.google.com:19302' }];
const DATA_CHANNEL_LABEL = 'secdogie-data';
const ROOM_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const PING_INTERVAL_MS = 20_000; // well under the gateway's 60 s idle timeout
const NEGOTIATION_TIMEOUT_MS = 30_000; // link created -> data channel open
const DISCONNECT_GRACE_MS = 8_000; // ICE 'disconnected' this long => link lost
const MAX_RENEGOTIATIONS = 3; // offerer retries after a lost link...
const RENEGOTIATION_BACKOFF_MS = 1_000; // ...waiting 1 s, 2 s, 4 s
const BUFFER_HIGH_WATER = 1024 * 1024; // sendData() waits above this...
const BUFFER_LOW_WATER = 256 * 1024; // ...until the send buffer drains below this

/** One peer: its running session (or null), its state and its listeners. */
function newPeer() {
  return { session: null, state: PeerState.IDLE, messageListeners: new Set(), stateListeners: new Set() };
}

/** The peer the module-level functions drive. */
const defaultPeer = newPeer();

/**
 * Joins `room` on the signaling gateway and brings up a direct data channel
 * with the other peer in it. Must be called from a user gesture handler.
 * Resolves once the gateway has admitted this peer; watch onStateChange() for
 * the P2P link itself ('connected').
 */
export function startPeerConnection(options = {}) {
  const { consented, ...rest } = options; // the default peer always asks for its own gesture
  return startOn(defaultPeer, rest);
}

async function startOn(peer, {
  signalingUrl,
  room = 'default',
  iceServers = DEFAULT_ICE_SERVERS,
  log = (line) => console.debug('[web_peer]', line),
  consented = false,
} = {}) {
  // Explicit consent: a page cannot bring the peer up silently on load. Browsers
  // without the UserActivation API cannot be checked; the demo page still only
  // calls this from a click handler. `consented` is for a caller that already
  // took the person's click for this whole session (mesh.js: 「上线」), and
  // reconnects its links while that session lasts.
  const activation = globalThis.navigator?.userActivation;
  if (!consented && activation && !activation.isActive) {
    throw new Error('startPeerConnection() must be called from a user gesture, e.g. a button click');
  }
  if (peer.session) throw new Error('peer is already running; call closePeerConnection() first');
  if (typeof RTCPeerConnection !== 'function') throw new Error('this browser does not support WebRTC');

  const s = {
    peer,
    url: signalingEndpoint(signalingUrl, room),
    room,
    iceServers,
    log,
    ws: null,
    peerId: null,
    remotePeerId: null, // the other peer currently in the room, per the gateway
    link: null, // the current RTCPeerConnection attempt, see createLink()
    pingTimer: null,
    retryTimer: null,
    retries: 0,
    settle: null, // resolve/reject of the promise returned below, until 'welcome'
  };
  const joined = new Promise((resolve, reject) => {
    s.settle = { resolve, reject };
  });
  peer.session = s;
  setState(s.peer, PeerState.SIGNALING, `connecting to ${s.url.host}`);
  try {
    openSignaling(s);
  } catch (err) {
    endSession(s, PeerState.FAILED, `cannot open signaling socket: ${err.message}`);
  }
  return joined;
}

/** Closes the data channel, the peer connection and the signaling socket. Safe to call at any time. */
export function closePeerConnection() {
  closeOn(defaultPeer);
}

function closeOn(peer) {
  if (peer.session) endSession(peer.session, PeerState.IDLE, 'closed by user');
}

/**
 * Sends over the reliable, ordered data channel. Strings and binary
 * (ArrayBuffer, typed arrays, Blob) go as-is; any other value is sent as JSON
 * text. Waits while the channel's send buffer is above the high-water mark.
 */
export function sendData(payload) {
  return sendOn(defaultPeer, payload);
}

async function sendOn(peer, payload) {
  const link = peer.session?.link;
  const channel = link?.channel;
  if (channel?.readyState !== 'open') throw new Error('no open data channel; wait for the "connected" state');

  const data = await encode(payload);
  const size = typeof data === 'string' ? new TextEncoder().encode(data).byteLength : data.byteLength;
  const limit = link.pc.sctp?.maxMessageSize;
  if (Number.isFinite(limit) && limit > 0 && size > limit) {
    throw new RangeError(`payload is ${size} bytes; this data channel accepts at most ${limit} per message`);
  }
  if (channel.bufferedAmount > BUFFER_HIGH_WATER) await drained(channel);
  if (channel.readyState !== 'open') throw new Error('data channel closed before the payload could be sent');
  channel.send(data);
}

/** Registers a listener for incoming data-channel messages; returns an unsubscribe function. */
export function onMessage(callback) {
  return subscribe(defaultPeer.messageListeners, callback);
}

/** Registers a listener for PeerState transitions; returns an unsubscribe function. */
export function onStateChange(callback) {
  return subscribe(defaultPeer.stateListeners, callback);
}

/**
 * The SDP of the current connection, local and remote, or null until both are
 * set. Read-only: pairing takes the DTLS fingerprints from it, because the
 * browser refuses a DTLS certificate that does not match the remote SDP's
 * fingerprint. A rebuilt connection has new certificates, so new fingerprints;
 * an ICE recovery keeps the same ones.
 */
export function currentDescriptions() {
  return descriptionsOf(defaultPeer);
}

/**
 * Another peer, independent of the default one and of each other: its own
 * signaling socket, room, link and listeners.
 */
export function createPeerLink() {
  const peer = newPeer();
  return Object.freeze({
    start: (options) => startOn(peer, options),
    close: () => closeOn(peer),
    send: (payload) => sendOn(peer, payload),
    onMessage: (callback) => subscribe(peer.messageListeners, callback),
    onStateChange: (callback) => subscribe(peer.stateListeners, callback),
    currentDescriptions: () => descriptionsOf(peer),
    get state() {
      return peer.state;
    },
  });
}

function descriptionsOf(peer) {
  const pc = peer.session?.link?.pc;
  const local = pc?.localDescription?.sdp;
  const remote = pc?.remoteDescription?.sdp;
  return local && remote ? { local, remote } : null;
}

// --- signaling --------------------------------------------------------------

function signalingEndpoint(signalingUrl, room) {
  if (!signalingUrl) throw new TypeError('signalingUrl is required, e.g. "wss://<worker>.workers.dev/ws"');
  const url = new URL(signalingUrl, globalThis.location?.href);
  if (url.protocol !== 'ws:' && url.protocol !== 'wss:') throw new TypeError('signalingUrl must be a ws: or wss: URL');
  if (!ROOM_PATTERN.test(room)) throw new TypeError('room must be 1-64 characters of [A-Za-z0-9_-]');
  url.searchParams.set('room', room);
  return url;
}

function openSignaling(s) {
  const ws = new WebSocket(s.url);
  s.ws = ws;
  ws.onopen = () => {
    if (s !== s.peer.session) return;
    s.log('signaling connected');
    s.pingTimer = setInterval(() => signal(s, { type: 'ping' }), PING_INTERVAL_MS);
  };
  ws.onmessage = (event) => {
    if (s !== s.peer.session) return;
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      s.log('ignored a malformed signaling message');
      return;
    }
    handleSignal(s, msg);
  };
  ws.onerror = () => {
    if (s === s.peer.session) s.log('signaling socket error');
  };
  ws.onclose = (event) => {
    if (s !== s.peer.session) return;
    clearInterval(s.pingTimer);
    const why = `signaling closed (${event.code}${event.reason ? `: ${event.reason}` : ''})`;
    // An established P2P link does not need the gateway any more.
    if (s.link?.channel?.readyState === 'open') s.log(`${why}; the direct link stays up`);
    else endSession(s, PeerState.FAILED, why);
  };
}

function handleSignal(s, msg) {
  switch (msg.type) {
    case 'welcome': {
      s.peerId = msg.peerId;
      s.log(`joined room "${s.room}" as ${short(msg.peerId)}`);
      s.settle?.resolve({ peerId: msg.peerId, room: s.room });
      s.settle = null;
      const [remote] = Array.isArray(msg.peers) ? msg.peers : [];
      if (remote) {
        s.remotePeerId = remote; // we joined second, so we make the offer
        startOffer(s);
      } else {
        setState(s.peer, PeerState.WAITING, 'waiting for a second peer to join the room');
      }
      break;
    }
    case 'peer-joined':
      s.remotePeerId = msg.peerId;
      s.retries = 0;
      s.log(`peer ${short(msg.peerId)} joined; waiting for its offer`);
      if (!s.link) setState(s.peer, PeerState.WAITING, 'peer joined; waiting for its offer');
      break;
    case 'peer-left':
      if (msg.peerId !== s.remotePeerId) break;
      s.remotePeerId = null;
      clearTimeout(s.retryTimer);
      if (s.link?.channel?.readyState === 'open') {
        s.log(`peer ${short(msg.peerId)} left signaling; the direct link stays up until ICE reports otherwise`);
      } else {
        dropLink(s);
        setState(s.peer, PeerState.WAITING, 'the other peer left the room');
      }
      break;
    case 'offer':
      handleOffer(s, msg.from, msg.payload);
      break;
    case 'answer':
      handleAnswer(s, msg.from, msg.payload);
      break;
    case 'ice-candidate':
      handleRemoteCandidate(s, msg.from, msg.payload);
      break;
    case 'error':
      s.log(`gateway error ${msg.code}: ${msg.message}`);
      break;
    default:
      s.log(`ignored signaling message of type "${msg.type}"`);
  }
}

function signal(s, msg) {
  if (s.ws?.readyState !== WebSocket.OPEN) {
    s.log(`signaling is not open; dropped ${msg.type}`);
    return;
  }
  s.ws.send(JSON.stringify(msg));
}

// --- negotiation ------------------------------------------------------------

/** Starts a fresh RTCPeerConnection attempt towards `remoteId`, replacing any previous one. */
function createLink(s, remoteId, role) {
  dropLink(s);
  clearTimeout(s.retryTimer);
  const pc = new RTCPeerConnection({ iceServers: s.iceServers });
  const link = {
    pc,
    role,
    remoteId,
    channel: null,
    descriptionSent: false,
    outbox: [], // local ICE candidates gathered before our offer/answer went out
    negotiationTimer: null,
    graceTimer: null,
  };
  s.link = link;

  pc.onicecandidate = ({ candidate }) => {
    if (!candidate || !isCurrent(s, link)) return; // null marks the end of gathering
    const msg = { type: 'ice-candidate', to: remoteId, payload: candidate.toJSON() };
    if (link.descriptionSent) signal(s, msg);
    else link.outbox.push(msg);
  };
  pc.oniceconnectionstatechange = () => {
    if (isCurrent(s, link)) onIceStateChange(s, link);
  };
  pc.ondatachannel = ({ channel }) => {
    if (!isCurrent(s, link) || link.channel) {
      channel.close(); // the protocol uses exactly one channel, opened by the offerer
      return;
    }
    attachChannel(s, link, channel);
  };
  link.negotiationTimer = setTimeout(() => linkLost(s, link, 'negotiation timed out'), NEGOTIATION_TIMEOUT_MS);
  setState(s.peer, PeerState.NEGOTIATING, `${role === 'offerer' ? 'offering to' : 'answering'} ${short(remoteId)}`);
  return link;
}

async function startOffer(s) {
  const link = createLink(s, s.remotePeerId, 'offerer');
  const { pc } = link;
  // Reliable and ordered: with neither maxRetransmits nor maxPacketLifeTime set,
  // SCTP retransmits every message until it is delivered, in order.
  attachChannel(s, link, pc.createDataChannel(DATA_CHANNEL_LABEL, { ordered: true }));
  try {
    const offer = await pc.createOffer();
    if (!isCurrent(s, link)) return;
    await pc.setLocalDescription(offer);
    if (!isCurrent(s, link)) return;
    sendDescription(s, link);
  } catch (err) {
    linkLost(s, link, `could not create an offer: ${err.message}`);
  }
}

async function handleOffer(s, from, description) {
  // An offer means the other side (re)started: whatever link we hold is stale.
  // createLink() and setRemoteDescription() run before the first await, so ICE
  // candidates that arrive right behind the offer queue up on the connection's
  // operations chain after it.
  s.remotePeerId = from;
  const link = createLink(s, from, 'answerer');
  const { pc } = link;
  try {
    await pc.setRemoteDescription(description);
    if (!isCurrent(s, link)) return;
    const answer = await pc.createAnswer();
    if (!isCurrent(s, link)) return;
    await pc.setLocalDescription(answer);
    if (!isCurrent(s, link)) return;
    sendDescription(s, link);
  } catch (err) {
    linkLost(s, link, `could not answer the offer: ${err.message}`);
  }
}

async function handleAnswer(s, from, description) {
  const link = s.link;
  if (link?.role !== 'offerer' || link.remoteId !== from || link.pc.signalingState !== 'have-local-offer') {
    s.log(`ignored an unexpected answer from ${short(from)}`);
    return;
  }
  try {
    await link.pc.setRemoteDescription(description);
  } catch (err) {
    linkLost(s, link, `could not apply the answer: ${err.message}`);
  }
}

function handleRemoteCandidate(s, from, candidate) {
  const link = s.link;
  if (link?.remoteId !== from) return; // belongs to a link we already dropped
  link.pc.addIceCandidate(candidate).catch((err) => {
    if (isCurrent(s, link)) s.log(`ignored an ICE candidate: ${err.message}`);
  });
}

function sendDescription(s, link) {
  const { type, sdp } = link.pc.localDescription;
  signal(s, { type, to: link.remoteId, payload: { type, sdp } });
  link.descriptionSent = true;
  for (const msg of link.outbox.splice(0)) signal(s, msg);
}

// --- link lifecycle ---------------------------------------------------------

function onIceStateChange(s, link) {
  const ice = link.pc.iceConnectionState;
  s.log(`ICE connection state: ${ice}`);
  switch (ice) {
    case 'connected':
    case 'completed':
      if (link.graceTimer) {
        clearTimeout(link.graceTimer);
        link.graceTimer = null;
        const open = link.channel?.readyState === 'open';
        setState(s.peer, open ? PeerState.CONNECTED : PeerState.NEGOTIATING, 'ICE connectivity recovered');
      }
      break;
    case 'disconnected':
      // The gateway already said the peer left, and now ICE agrees: it is gone.
      if (s.remotePeerId !== link.remoteId) {
        linkLost(s, link, 'ICE disconnected after the peer left the room');
        break;
      }
      // Otherwise often transient (Wi-Fi hiccup, NAT rebinding): give ICE a
      // grace period to recover on its own before declaring the link lost.
      if (!link.graceTimer) {
        setState(s.peer, PeerState.DISCONNECTED, 'ICE connectivity lost; waiting for it to recover');
        link.graceTimer = setTimeout(() => linkLost(s, link, 'ICE did not recover in time'), DISCONNECT_GRACE_MS);
      }
      break;
    case 'failed':
      linkLost(s, link, 'ICE failed');
      break;
    default:
      break; // new / checking / closed need no action
  }
}

function attachChannel(s, link, channel) {
  link.channel = channel;
  channel.binaryType = 'arraybuffer';
  channel.bufferedAmountLowThreshold = BUFFER_LOW_WATER;
  channel.onopen = () => {
    if (!isCurrent(s, link)) return;
    clearTimeout(link.negotiationTimer);
    s.retries = 0;
    setState(s.peer, PeerState.CONNECTED, `direct data channel open with ${short(link.remoteId)}`);
  };
  channel.onclose = () => {
    if (isCurrent(s, link)) linkLost(s, link, 'data channel closed');
  };
  channel.onerror = (event) => {
    if (isCurrent(s, link)) s.log(`data channel error: ${event.error?.message ?? 'unknown'}`);
  };
  channel.onmessage = (event) => {
    if (isCurrent(s, link)) emit(s.peer.messageListeners, event.data);
  };
}

/** The current link is gone: drop it, and let the offerer try again if the peer is still around. */
function linkLost(s, link, reason) {
  if (!isCurrent(s, link)) return;
  dropLink(s);
  if (s.ws?.readyState !== WebSocket.OPEN) {
    endSession(s, PeerState.FAILED, `${reason}; signaling is gone, so the link cannot be rebuilt`);
    return;
  }
  setState(s.peer, PeerState.WAITING, reason);
  if (link.role === 'offerer' && s.remotePeerId === link.remoteId && s.retries < MAX_RENEGOTIATIONS) {
    const delay = RENEGOTIATION_BACKOFF_MS * 2 ** s.retries;
    s.retries += 1;
    s.log(`re-offering in ${delay} ms (attempt ${s.retries}/${MAX_RENEGOTIATIONS})`);
    s.retryTimer = setTimeout(() => {
      if (s === s.peer.session && !s.link && s.remotePeerId) startOffer(s);
    }, delay);
  }
}

function dropLink(s) {
  const link = s.link;
  if (!link) return;
  s.link = null;
  clearTimeout(link.negotiationTimer);
  clearTimeout(link.graceTimer);
  link.channel?.close();
  link.pc.close();
}

function endSession(s, finalState, reason) {
  if (s !== s.peer.session) return;
  s.peer.session = null;
  clearInterval(s.pingTimer);
  clearTimeout(s.retryTimer);
  dropLink(s);
  if (s.ws && s.ws.readyState <= WebSocket.OPEN) s.ws.close(1000, 'peer closed');
  s.settle?.reject(new Error(reason));
  s.settle = null;
  setState(s.peer, finalState, reason);
}

// --- helpers ----------------------------------------------------------------

function isCurrent(s, link) {
  return s === s.peer.session && s.link === link;
}

function setState(peer, next, detail) {
  if (next === peer.state) return;
  peer.state = next;
  emit(peer.stateListeners, next, detail);
}

function subscribe(listeners, callback) {
  if (typeof callback !== 'function') throw new TypeError('callback must be a function');
  listeners.add(callback);
  return () => listeners.delete(callback);
}

function emit(listeners, ...args) {
  for (const callback of [...listeners]) {
    try {
      callback(...args);
    } catch (err) {
      console.error('[web_peer] listener threw', err);
    }
  }
}

async function encode(payload) {
  if (typeof payload === 'string' || payload instanceof ArrayBuffer || ArrayBuffer.isView(payload)) return payload;
  if (typeof Blob !== 'undefined' && payload instanceof Blob) return payload.arrayBuffer();
  const json = JSON.stringify(payload);
  if (json === undefined) throw new TypeError('payload must be a string, binary data, or a JSON-serialisable value');
  return json;
}

function drained(channel) {
  return new Promise((resolve) => {
    const done = () => {
      channel.removeEventListener('bufferedamountlow', done);
      channel.removeEventListener('close', done);
      resolve();
    };
    channel.addEventListener('bufferedamountlow', done);
    channel.addEventListener('close', done);
  });
}

function short(id) {
  return String(id).slice(0, 8);
}
