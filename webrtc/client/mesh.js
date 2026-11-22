// A browser device in your mesh: goes online when the person clicks, links
// directly to each of your other devices that is online, and keeps the roster
// in step with them (PLAN.md, "Your own devices", S5).
//
// One WebRTC link per pair of devices, each in its own signaling room
// (meshlink.js linkRoom). On every new connection both ends sign a link-hello
// over the DTLS fingerprints they see; until the other end's hello checks out
// nothing else is read. After that, everything is a DID-signed frame
// (frames.js) on the mux channel mesh/v1:
//
//   {m: "roster", roster}   the admissions this device holds; a newcomer
//                           learns the rest of the mesh this way and links
//                           to it
//   {m: "note", text}       a line from the person, shown on the others
//   {m: "ping", t} / pong   round-trip time for the device list
//
// Nothing starts at import, and nothing runs in the background: goOnline()
// needs the person's click; goOffline() or closing the page ends it all.

import { decodeFrame, encodeFrame, muxDecode, muxEncode, newCounter, ReplayWindow } from './frames.js';
import { MESH_CHANNEL, Roster, createHello, linkRoom, verifyHello } from './meshlink.js';
import { cleanName, fingerprintFromSdp, randomHex32 } from './pairing.js';
import { PeerState, createPeerLink } from './web_peer.js';

export const LinkState = Object.freeze({
  OFFLINE: 'offline', // not online, or waiting in the room for that device
  CONNECTING: 'connecting', // negotiating, or checking each other's hello
  ONLINE: 'online', // a direct link that checked out
  REFUSED: 'refused', // its hello did not check out; see `reason`
});

export const LinkTier = Object.freeze({
  LAN: 'lan',
  WEBRTC: 'webrtc',
  RELAY: 'relay',
});

export const StarRole = Object.freeze({
  HUB: 'hub',
  SPOKE: 'spoke',
});

/**
 * Outbound monotonic seq_id buffer for mobile background disconnect/reconnect.
 */
export class SequencedFrameQueue {
  constructor(maxBuffer = 512) {
    this.maxBuffer = Math.max(1, maxBuffer);
    this._nextSeq = 0;
    this._lastAck = 0;
    this._pending = [];
  }

  get lastSeq() { return this._nextSeq; }
  get lastAckSeq() { return this._lastAck; }
  get pendingCount() { return this._pending.length; }

  enqueue(payload) {
    this._nextSeq += 1;
    const frame = { ...payload, seq_id: this._nextSeq };
    this._pending.push(frame);
    while (this._pending.length > this.maxBuffer) this._pending.shift();
    return frame;
  }

  ack(lastAckSeq) {
    const seq = Number(lastAckSeq) | 0;
    if (seq <= this._lastAck) return 0;
    this._lastAck = this._nextSeq ? Math.min(seq, this._nextSeq) : seq;
    let trimmed = 0;
    while (this._pending.length > 0 && this._pending[0].seq_id <= seq) {
      this._pending.shift();
      trimmed += 1;
    }
    return trimmed;
  }

  replayAfter(lastAckSeq) {
    const seq = Math.max(0, Number(lastAckSeq) | 0);
    if (seq > this._lastAck) this.ack(seq);
    return this._pending.filter((f) => f.seq_id > seq).map((f) => ({ ...f }));
  }

  resumeMessage(lastReceivedSeq) {
    return { m: 'resume', last_ack_seq: Math.max(0, Number(lastReceivedSeq) | 0) };
  }
}

/**
 * Inbound seq_id deduplicator so replayed control/approval frames never run twice.
 */
export class IdempotentReceiver {
  constructor(maxSeen = 2048) {
    this.maxSeen = Math.max(16, maxSeen);
    this._lastDelivered = 0;
    this._seen = new Set();
    this._order = [];
  }

  get lastDeliveredSeq() { return this._lastDelivered; }

  accept(seqId) {
    const seq = Number(seqId) | 0;
    if (seq <= 0) return { isNew: false, ackSeq: this._lastDelivered };
    if (this._seen.has(seq) || seq <= this._lastDelivered) {
      return { isNew: false, ackSeq: Math.max(this._lastDelivered, seq) };
    }
    this._seen.add(seq);
    this._order.push(seq);
    while (this._order.length > this.maxSeen) {
      const old = this._order.shift();
      this._seen.delete(old);
    }
    if (seq > this._lastDelivered) this._lastDelivered = seq;
    return { isNew: true, ackSeq: this._lastDelivered };
  }
}

const PING_EVERY_MS = 4000;
const RESEND_MS = 700; // a hello not yet acknowledged goes out again this often...
const RESEND_MAX = 40; // ...this many times at most
const RETRY_MS = [2000, 4000, 8000, 15000, 30000];
const MAX_NOTE = 500;
const utf8 = new TextEncoder();
const fromUtf8 = new TextDecoder();

/**
 * me          this device's identity (pairing.js / keystore.js)
 * mesh        the mesh's name
 * owners      the owners' DIDs (an owner device: itself; a member: its admission's signer)
 * admission   this device's own admission, or null on an owner
 * admissions  admissions already known (an owner: every device it admitted)
 * name        what this device is called
 */
export async function createMesh({
  me, mesh, owners, admission = null, admissions = [], name = '', signalingUrl, iceServers,
  role = StarRole.SPOKE, hubDid = null, defaultTier = LinkTier.WEBRTC, log = () => {},
}) {
  const starRole = role === StarRole.HUB ? StarRole.HUB : StarRole.SPOKE;
  const pinnedHub = hubDid || (starRole === StarRole.HUB ? me.did : (owners?.[0] ?? null));
  const roster = new Roster(mesh, owners);
  for (const a of [admission, ...admissions]) if (a) await roster.admit(a);
  const links = new Map(); // did -> link entry
  // An owner key is not always a device: when Studio owns the mesh, its operator
  // key signs admissions but never goes online (Studio links as its node). So an
  // owner with no admission of its own is listed once it has been seen online.
  const seenOnline = new Set();
  const listed = (did) => did !== me.did && (roster.admission(did) || !roster.owners.has(did) || seenOnline.has(did));
  const changeListeners = new Set();
  const noteListeners = new Set();
  const controlListeners = new Set();
  const nextCtr = newCounter();
  let online = false;
  let pinger = null;
  let signal = signalingUrl;

  const emit = (set, ...args) => {
    for (const cb of [...set]) {
      try {
        cb(...args);
      } catch (err) {
        console.error('[mesh] listener threw', err);
      }
    }
  };
  const changed = () => emit(changeListeners, snapshot());

  function snapshot() {
    return {
      online,
      mesh,
      role: starRole,
      hubDid: pinnedHub,
      me: { did: me.did, name: cleanName(name), owner: roster.owners.has(me.did), role: starRole },
      devices: roster.devices().filter(listed).map((did) => {
        const l = links.get(did);
        return {
          did,
          name: l?.name || roster.name(did),
          owner: roster.owners.has(did),
          role: l?.role || (did === pinnedHub ? StarRole.HUB : StarRole.SPOKE),
          tier: l?.tier || defaultTier,
          unacked: l ? l.outbound.pendingCount : 0,
          state: online && l ? l.state : LinkState.OFFLINE,
          rtt: l?.state === LinkState.ONLINE ? l.rtt : null,
          reason: l?.reason ?? null,
        };
      }),
    };
  }

  // ---- links -----------------------------------------------------------------------

  async function ensureLink(did) {
    if (!online || did === me.did || links.has(did)) return;
    const entry = {
      did, peer: createPeerLink(), room: await linkRoom(mesh, me.did, did), state: LinkState.OFFLINE,
      connection: null, fps: null, authed: false, theirHelloSig: null, myHello: null, peerHasMine: false,
      resendTimer: null, name: roster.name(did), rtt: null, reason: null, window: new ReplayWindow(),
      role: did === pinnedHub ? StarRole.HUB : StarRole.SPOKE, tier: defaultTier,
      outbound: new SequencedFrameQueue(), inbound: new IdempotentReceiver(),
      attempts: 0, retryTimer: null, chain: Promise.resolve(), unsubscribe: [],
    };
    if (!online || links.has(did)) return;
    links.set(did, entry);
    entry.unsubscribe.push(entry.peer.onStateChange((state, detail) => onPeerState(entry, state, detail)));
    entry.unsubscribe.push(entry.peer.onMessage((data) => {
      entry.chain = entry.chain.then(() => onPeerMessage(entry, data)).catch((err) => log(`link ${short(did)}: ${err.message}`));
    }));
    start(entry);
  }

  function start(entry) {
    entry.peer.start({ signalingUrl: signal, room: entry.room, iceServers, consented: true, log: (l) => log(`${short(entry.did)} ${l}`) })
      .catch((err) => {
        log(`${short(entry.did)} could not start: ${err.message}`);
        retry(entry);
      });
  }

  function retry(entry) {
    if (!online || links.get(entry.did) !== entry) return;
    clearTimeout(entry.retryTimer);
    const delay = RETRY_MS[Math.min(entry.attempts, RETRY_MS.length - 1)];
    entry.attempts += 1;
    entry.retryTimer = setTimeout(() => {
      if (online && links.get(entry.did) === entry && entry.peer.state === PeerState.IDLE) start(entry);
      else if (online && links.get(entry.did) === entry && entry.peer.state === PeerState.FAILED) start(entry);
    }, delay);
  }

  function setLink(entry, state, reason = null) {
    entry.state = state;
    entry.reason = reason;
    if (state !== LinkState.ONLINE) entry.rtt = null;
    changed();
  }

  function onPeerState(entry, state) {
    if (!online || links.get(entry.did) !== entry) return;
    if (state === PeerState.CONNECTED) {
      connected(entry).catch((err) => log(`${short(entry.did)} hello failed: ${err.message}`));
    } else if (state === PeerState.NEGOTIATING || state === PeerState.DISCONNECTED) {
      if (entry.state !== LinkState.REFUSED) setLink(entry, LinkState.CONNECTING);
    } else if (state === PeerState.WAITING || state === PeerState.SIGNALING) {
      forgetConnection(entry);
      if (entry.state !== LinkState.REFUSED) setLink(entry, LinkState.OFFLINE);
    } else if (state === PeerState.FAILED || state === PeerState.IDLE) {
      forgetConnection(entry);
      if (entry.state !== LinkState.REFUSED) setLink(entry, LinkState.OFFLINE);
      retry(entry);
    }
  }

  /**
   * This connection, by the DTLS fingerprints both ends see. A new connection
   * starts the hellos over; the same one (ICE recovered) keeps them. The other
   * end's hello and our own `connected` may come in either order, so both
   * paths go through here.
   */
  function connectionOf(entry) {
    const d = entry.peer.currentDescriptions();
    const fps = d && { local: fingerprintFromSdp(d.local), remote: fingerprintFromSdp(d.remote) };
    if (!fps?.local || !fps?.remote) return null;
    const key = `${fps.local}|${fps.remote}`;
    if (entry.connection !== key) {
      forgetConnection(entry);
      entry.connection = key;
      entry.fps = fps;
    }
    return fps;
  }

  function forgetConnection(entry) {
    clearInterval(entry.resendTimer);
    Object.assign(entry, {
      connection: null, fps: null,
      authed: false, // their hello on this connection has not checked out yet
      theirHelloSig: null,
      myHello: null, // ours for this connection (a promise: signed once)
      peerHasMine: false, // they told us they have ours
      resendTimer: null,
    });
  }

  function sendHello(entry, hello) {
    return entry.peer.send(JSON.stringify({ k: 'hello', hello, got: entry.authed }));
  }

  /**
   * Our hello on this connection: signed once, sent now, and sent again until
   * the other end says it has it. A browser can drop a message sent the instant
   * a data channel opens (about once in fifty links, also with bare
   * RTCPeerConnections), and a link must not hang on that. Every frame waits
   * behind the first send, or the other end would drop the frames.
   */
  async function sayHello(entry) {
    const { connection, fps } = entry;
    entry.myHello ??= roster.digest().then((digest) => createHello(me, {
      mesh, nonce: randomHex32(), localFp: fps.local, remoteFp: fps.remote, admission, name, roster: digest,
    }));
    const hello = await entry.myHello;
    if (entry.connection !== connection) return; // a newer connection took over meanwhile
    await sendHello(entry, hello);
    if (!entry.resendTimer && !entry.peerHasMine) {
      let left = RESEND_MAX;
      entry.resendTimer = setInterval(() => {
        const stale = !online || links.get(entry.did) !== entry || entry.connection !== connection;
        if (stale || entry.peerHasMine || --left < 0) {
          clearInterval(entry.resendTimer);
          entry.resendTimer = null;
          return;
        }
        sendHello(entry, hello).catch(() => {});
      }, RESEND_MS);
    }
  }

  /** The data channel is open: say who we are, over what we see of this connection. */
  async function connected(entry) {
    if (!connectionOf(entry)) throw new Error('no DTLS fingerprints in the session description');
    entry.attempts = 0;
    setLink(entry, entry.authed ? LinkState.ONLINE : LinkState.CONNECTING);
    await sayHello(entry);
  }

  async function onPeerMessage(entry, data) {
    if (!online || links.get(entry.did) !== entry || typeof data !== 'string' || data.length > 65536) return;
    if (data.startsWith('{"k":"hello"')) {
      await theirHello(entry, data);
      return;
    }
    if (!entry.authed) return; // nothing but a hello counts before their hello checks out
    const frame = await decodeFrame(data, { allow: [entry.did], selfDid: me.did });
    if (!frame || !entry.window.accept(frame.ctr)) return;
    entry.peerHasMine = true; // they send frames only once our hello checked out with them
    const inner = muxDecode(frame.message);
    if (inner?.channel !== MESH_CHANNEL) return;
    let msg;
    try {
      msg = JSON.parse(fromUtf8.decode(inner.payload));
    } catch {
      return;
    }
    await onMeshMessage(entry, msg);
  }

  async function theirHello(entry, data) {
    let msg;
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }
    if (msg?.k !== 'hello') return;
    const fps = connectionOf(entry);
    if (!fps) return;
    if (msg.got === true) entry.peerHasMine = true;
    if (entry.authed) {
      // a resend of the hello we already took: ours (or what followed it) may not have reached them
      if (msg.hello?.sig !== entry.theirHelloSig) return;
      if (msg.got !== true && entry.myHello) await sendHello(entry, await entry.myHello);
      if (msg.hello.roster !== (await roster.digest())) await sendTo(entry, { m: 'roster', roster: roster.wire() });
      return;
    }
    const checked = await verifyHello(msg.hello, {
      roster, expectDid: entry.did, myLocalFp: fps.local, myRemoteFp: fps.remote,
    });
    if (!checked.ok) {
      log(`${short(entry.did)} refused: ${checked.reason}`);
      setLink(entry, LinkState.REFUSED, checked.reason);
      entry.peer.close(); // retried later, in case its admission reaches us by then
      return;
    }
    entry.authed = true;
    seenOnline.add(entry.did);
    entry.theirHelloSig = checked.value.sig;
    if (checked.value.admission) await roster.admit(checked.value.admission);
    entry.name = checked.value.name || roster.name(entry.did);
    setLink(entry, LinkState.ONLINE);
    await sayHello(entry); // ours goes out before any frame, or the other end would drop the frames
    if (checked.value.roster !== (await roster.digest())) await sendTo(entry, { m: 'roster', roster: roster.wire() });
    await sendTo(entry, { m: 'ping', t: performance.now() });
    await sendTo(entry, entry.outbound.resumeMessage(entry.inbound.lastDeliveredSeq));
  }

  async function onMeshMessage(entry, msg) {
    const kind = msg?.m;
    if ((kind === 'note' || kind === 'control') && Number.isInteger(msg.seq_id)) {
      const { isNew, ackSeq } = entry.inbound.accept(msg.seq_id);
      await sendTo(entry, { m: 'ack', seq_id: ackSeq });
      if (!isNew) return;
    }
    switch (kind) {
      case 'resume':
        if (Number.isInteger(msg.last_ack_seq)) {
          for (const pending of entry.outbound.replayAfter(msg.last_ack_seq)) {
            await sendTo(entry, pending);
          }
          changed();
        }
        break;
      case 'ack':
        if (Number.isInteger(msg.seq_id) && entry.outbound.ack(msg.seq_id) > 0) {
          changed();
        }
        break;
      case 'roster': {
        const added = await roster.merge(msg.roster);
        if (added > 0) {
          changed();
          for (const did of roster.devices()) await ensureLink(did);
          for (const other of links.values()) {
            if (other !== entry && other.authed) await sendTo(other, { m: 'roster', roster: roster.wire() });
          }
        }
        break;
      }
      case 'note':
        if (typeof msg.text === 'string' && msg.text.trim()) {
          emit(noteListeners, { from: entry.did, name: entry.name, text: msg.text.slice(0, MAX_NOTE), at: Date.now() });
        }
        break;
      case 'control':
        if (msg.body && typeof msg.body === 'object') {
          if (starRole === StarRole.SPOKE && pinnedHub && entry.did !== pinnedHub) break;
          emit(controlListeners, { from: entry.did, body: msg.body, seq_id: msg.seq_id ?? null });
        }
        break;
      case 'ping':
        await sendTo(entry, { m: 'pong', t: msg.t });
        break;
      case 'pong':
        if (typeof msg.t === 'number') {
          entry.rtt = Math.max(0, Math.round(performance.now() - msg.t));
          changed();
        }
        break;
      default:
        break;
    }
  }

  async function sendTo(entry, msg) {
    if (!entry.authed) return false;
    const frame = await encodeFrame(me, entry.did, muxEncode(MESH_CHANNEL, utf8.encode(JSON.stringify(msg))), nextCtr());
    try {
      await entry.peer.send(frame);
      return true;
    } catch {
      return false;
    }
  }

  function dropAll() {
    for (const entry of links.values()) {
      clearTimeout(entry.retryTimer);
      clearInterval(entry.resendTimer);
      for (const off of entry.unsubscribe) off();
      entry.peer.close();
    }
    links.clear();
  }

  // ---- what the page uses -----------------------------------------------------------

  return Object.freeze({
    roster,
    /** Go online: link to every device in the roster. Needs the person's click. */
    async goOnline({ signalingUrl: url } = {}) {
      const activation = globalThis.navigator?.userActivation;
      if (activation && !activation.isActive) throw new Error('going online needs a click');
      if (online) return;
      if (url) signal = url;
      online = true;
      changed();
      for (const did of roster.devices()) await ensureLink(did);
      pinger = setInterval(() => {
        for (const entry of links.values()) if (entry.authed) sendTo(entry, { m: 'ping', t: performance.now() });
      }, PING_EVERY_MS);
    },
    /** Go offline: every link closes now. */
    goOffline() {
      if (!online) return;
      online = false;
      clearInterval(pinger);
      dropAll();
      changed();
    },
    /** Send a line to every device online now; resolves how many it reached. */
    async say(text) {
      const line = String(text ?? '').trim().slice(0, MAX_NOTE);
      if (!line) return 0;
      let n = 0;
      for (const entry of links.values()) {
        const framed = entry.outbound.enqueue({ m: 'note', text: line });
        if (await sendTo(entry, framed)) n += 1;
      }
      return n;
    },
    /** Send a sequenced control/approval frame to a specific peer (resumed on reconnect). */
    async sendControl(did, body) {
      const entry = links.get(did);
      if (!entry) return 0;
      const framed = entry.outbound.enqueue({ m: 'control', body: { ...body } });
      await sendTo(entry, framed);
      changed();
      return framed.seq_id;
    },
    snapshot,
    onChange(cb) {
      changeListeners.add(cb);
      return () => changeListeners.delete(cb);
    },
    onNote(cb) {
      noteListeners.add(cb);
      return () => noteListeners.delete(cb);
    },
    onControl(cb) {
      controlListeners.add(cb);
      return () => controlListeners.delete(cb);
    },
  });
}

function short(did) {
  return did ? `${did.slice(8, 14)}…${did.slice(-4)}` : '?';
}
