// A browser device in your mesh: goes online when the person clicks, links
// directly to each of your other devices that is online, and keeps the roster
// in step with them (docs/PLAN-OWN-DEVICES.zh.md, S5).
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
  me, mesh, owners, admission = null, admissions = [], name = '', signalingUrl, iceServers, log = () => {},
}) {
  const roster = new Roster(mesh, owners);
  for (const a of [admission, ...admissions]) if (a) await roster.admit(a);
  const links = new Map(); // did -> link entry
  const changeListeners = new Set();
  const noteListeners = new Set();
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
      me: { did: me.did, name: cleanName(name), owner: roster.owners.has(me.did) },
      devices: roster.devices().filter((d) => d !== me.did).map((did) => {
        const l = links.get(did);
        return {
          did,
          name: l?.name || roster.name(did),
          owner: roster.owners.has(did),
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
    entry.theirHelloSig = checked.value.sig;
    if (checked.value.admission) await roster.admit(checked.value.admission);
    entry.name = checked.value.name || roster.name(entry.did);
    setLink(entry, LinkState.ONLINE);
    await sayHello(entry); // ours goes out before any frame, or the other end would drop the frames
    if (checked.value.roster !== (await roster.digest())) await sendTo(entry, { m: 'roster', roster: roster.wire() });
    await sendTo(entry, { m: 'ping', t: performance.now() });
  }

  async function onMeshMessage(entry, msg) {
    switch (msg?.m) {
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
      for (const entry of links.values()) if (await sendTo(entry, { m: 'note', text: line })) n += 1;
      return n;
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
  });
}

function short(did) {
  return did ? `${did.slice(8, 14)}…${did.slice(-4)}` : '?';
}
