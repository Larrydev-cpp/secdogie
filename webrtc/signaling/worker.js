/**
 * secdogie WebRTC signaling gateway — Cloudflare Worker + Durable Object.
 *
 * A pure relay for the three WebRTC negotiation messages (`offer`, `answer`,
 * `ice-candidate`). It never inspects SDP, never sees application data (that
 * flows peer-to-peer over the DTLS-encrypted data channel), and stores nothing:
 * its only state is an in-memory map of peer ID -> live WebSocket session.
 *
 * Why a Durable Object: a plain Worker may run the two WebSockets of one room
 * in different isolates, where a module-level Map is not shared, so routing
 * would silently fail in production. `idFromName(room)` pins every socket of a
 * room to one object, and the map lives there.
 *
 * Wire protocol (JSON text frames):
 *   client -> gateway  {type: 'offer' | 'answer' | 'ice-candidate', to, payload}
 *                      {type: 'ping'}                  heartbeat, never relayed
 *   gateway -> client  {type: 'welcome', peerId, peers}   own ID + peers already present
 *                      {type: 'peer-joined', peerId}
 *                      {type: 'peer-left', peerId}
 *                      {type: 'offer' | 'answer' | 'ice-candidate', from, payload}
 *                      {type: 'error', code, message}
 *
 * `from` is always stamped by the gateway from the sender's own session, so a
 * peer cannot impersonate another. Payloads are re-built from a whitelist of
 * fields, so nothing beyond the standard RTCSessionDescriptionInit /
 * RTCIceCandidateInit shapes is forwarded.
 */

const LIMITS = Object.freeze({
  MAX_MESSAGE_BYTES: 16 * 1024, // a data-channel-only SDP is ~1 KB; this is generous
  MAX_PEERS_PER_ROOM: 2, // one-to-one: the room is the pairing
  RATE_BURST: 40, // token bucket: a full trickle-ICE burst fits comfortably...
  RATE_REFILL_PER_SEC: 10, // ...while a flood gets the socket closed
  IDLE_TIMEOUT_MS: 60_000, // clients ping every 20 s
  SWEEP_INTERVAL_MS: 30_000,
});

const RELAY_TYPES = new Set(['offer', 'answer', 'ice-candidate']);
const ROOM_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

const CLOSE_NORMAL = 1000;
const CLOSE_UNSUPPORTED_DATA = 1003;
const CLOSE_POLICY_VIOLATION = 1008;
const CLOSE_MESSAGE_TOO_BIG = 1009;
const CLOSE_INTERNAL_ERROR = 1011;
const CLOSE_ROOM_FULL = 4001;
const CLOSE_IDLE = 4002;

const utf8 = new TextEncoder();

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/health') return new Response('ok\n');
    if (url.pathname !== '/ws') return new Response('not found\n', { status: 404 });

    if (request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') {
      return new Response('expected a WebSocket upgrade\n', { status: 426, headers: { Upgrade: 'websocket' } });
    }
    if (!originAllowed(request.headers.get('Origin'), env.ALLOWED_ORIGINS)) {
      return new Response('origin not allowed\n', { status: 403 });
    }
    const room = url.searchParams.get('room') ?? '';
    if (!ROOM_PATTERN.test(room)) {
      return new Response('room must be 1-64 characters of [A-Za-z0-9_-]\n', { status: 400 });
    }

    const stub = env.SIGNALING_ROOMS.get(env.SIGNALING_ROOMS.idFromName(room));
    return stub.fetch(request);
  },
};

/** One signaling room: routes messages between the (at most two) peers in it. */
export class SignalingRoom {
  static LIMITS = LIMITS;

  constructor(state, env, clock = () => Date.now()) {
    this.state = state;
    this.clock = clock;
    /** @type {Map<string, {id: string, ws: WebSocket, lastSeen: number, tokens: number, refilledAt: number}>} */
    this.sessions = new Map();
  }

  async fetch() {
    const { 0: client, 1: server } = new WebSocketPair();
    this.join(server);
    return new Response(null, { status: 101, webSocket: client });
  }

  /** Accepts a server-side socket into the room; returns its session, or null if the room is full. */
  join(ws) {
    ws.accept();
    if (this.sessions.size >= LIMITS.MAX_PEERS_PER_ROOM) {
      sendJson(ws, { type: 'error', code: 'room-full', message: `room already has ${LIMITS.MAX_PEERS_PER_ROOM} peers` });
      safeClose(ws, CLOSE_ROOM_FULL, 'room full');
      return null;
    }

    const now = this.clock();
    const session = { id: crypto.randomUUID(), ws, lastSeen: now, tokens: LIMITS.RATE_BURST, refilledAt: now };
    const peers = [...this.sessions.keys()];
    this.sessions.set(session.id, session);

    ws.addEventListener('message', (event) => this.onMessage(session, event.data));
    ws.addEventListener('close', () => this.drop(session, CLOSE_NORMAL, 'closed'));
    ws.addEventListener('error', () => this.drop(session, CLOSE_INTERNAL_ERROR, 'socket error'));

    this.send(session, { type: 'welcome', peerId: session.id, peers });
    this.broadcast(session, { type: 'peer-joined', peerId: session.id });
    this.scheduleSweep().catch((err) => console.error('signaling: could not schedule idle sweep', err));
    return session;
  }

  onMessage(session, data) {
    if (this.sessions.get(session.id) !== session) return;
    const now = this.clock();
    session.lastSeen = now;

    // Flow control first, before any parsing work is spent on the frame.
    if (!takeToken(session, now)) return this.drop(session, CLOSE_POLICY_VIOLATION, 'rate limit exceeded');
    if (typeof data !== 'string') return this.drop(session, CLOSE_UNSUPPORTED_DATA, 'binary frames are not accepted');
    // UTF-8 is never shorter than the UTF-16 length, so the cheap check can reject first.
    if (data.length > LIMITS.MAX_MESSAGE_BYTES || utf8.encode(data).byteLength > LIMITS.MAX_MESSAGE_BYTES) {
      return this.drop(session, CLOSE_MESSAGE_TOO_BIG, 'message too large');
    }

    let msg;
    try {
      msg = JSON.parse(data);
    } catch {
      return this.reject(session, 'bad-json', 'message is not valid JSON');
    }
    if (msg === null || typeof msg !== 'object' || Array.isArray(msg)) {
      return this.reject(session, 'bad-message', 'message must be a JSON object');
    }
    if (msg.type === 'ping') return; // heartbeat: lastSeen is already refreshed
    if (!RELAY_TYPES.has(msg.type)) {
      return this.reject(session, 'unsupported-type', `type must be one of: ${[...RELAY_TYPES].join(', ')}`);
    }

    const payload = sanitizePayload(msg.type, msg.payload);
    if (!payload) return this.reject(session, 'bad-payload', `invalid ${msg.type} payload`);

    const target = typeof msg.to === 'string' ? this.sessions.get(msg.to) : undefined;
    if (!target || target === session) return this.reject(session, 'unknown-peer', 'target peer is not in this room');

    this.send(target, { type: msg.type, from: session.id, payload });
  }

  /** Removes a session, closes its socket, and tells the rest of the room. Idempotent. */
  drop(session, code, reason) {
    if (this.sessions.get(session.id) !== session) return;
    this.sessions.delete(session.id);
    if (code === CLOSE_POLICY_VIOLATION || code === CLOSE_MESSAGE_TOO_BIG || code === CLOSE_UNSUPPORTED_DATA) {
      console.warn(`signaling: closing ${session.id}: ${reason}`);
    }
    safeClose(session.ws, code, reason);
    this.broadcast(null, { type: 'peer-left', peerId: session.id });
  }

  send(session, msg) {
    if (!sendJson(session.ws, msg)) this.drop(session, CLOSE_INTERNAL_ERROR, 'send failed');
  }

  broadcast(except, msg) {
    for (const session of [...this.sessions.values()]) {
      if (session !== except) this.send(session, msg);
    }
  }

  reject(session, code, message) {
    this.send(session, { type: 'error', code, message });
  }

  async scheduleSweep() {
    if ((await this.state.storage.getAlarm()) === null) {
      await this.state.storage.setAlarm(this.clock() + LIMITS.SWEEP_INTERVAL_MS);
    }
  }

  /** Durable Object alarm: closes sessions that stopped sending (even pings), then re-arms while anyone is left. */
  async alarm() {
    const cutoff = this.clock() - LIMITS.IDLE_TIMEOUT_MS;
    for (const session of [...this.sessions.values()]) {
      if (session.lastSeen < cutoff) this.drop(session, CLOSE_IDLE, 'idle timeout');
    }
    if (this.sessions.size > 0) await this.state.storage.setAlarm(this.clock() + LIMITS.SWEEP_INTERVAL_MS);
  }
}

function takeToken(session, now) {
  const refill = ((now - session.refilledAt) / 1000) * LIMITS.RATE_REFILL_PER_SEC;
  session.tokens = Math.min(LIMITS.RATE_BURST, session.tokens + refill);
  session.refilledAt = now;
  if (session.tokens < 1) return false;
  session.tokens -= 1;
  return true;
}

/** Re-builds a payload from the standard fields only; returns null if it is not a valid one. */
function sanitizePayload(type, payload) {
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) return null;
  if (type === 'offer' || type === 'answer') {
    if (payload.type !== type || typeof payload.sdp !== 'string') return null;
    return { type, sdp: payload.sdp };
  }
  // ice-candidate (RTCIceCandidateInit); an empty candidate string means end-of-candidates.
  if (typeof payload.candidate !== 'string') return null;
  return {
    candidate: payload.candidate,
    sdpMid: typeof payload.sdpMid === 'string' ? payload.sdpMid : null,
    sdpMLineIndex: Number.isInteger(payload.sdpMLineIndex) ? payload.sdpMLineIndex : null,
    usernameFragment: typeof payload.usernameFragment === 'string' ? payload.usernameFragment : null,
  };
}

/** `allowList` is a comma-separated list of page origins; empty means any origin (local development). */
function originAllowed(origin, allowList) {
  const allowed = (allowList ?? '').split(',').map((entry) => entry.trim()).filter(Boolean);
  return allowed.length === 0 || (origin !== null && allowed.includes(origin));
}

function sendJson(ws, msg) {
  try {
    ws.send(JSON.stringify(msg));
    return true;
  } catch {
    return false;
  }
}

function safeClose(ws, code, reason) {
  try {
    ws.close(code, reason);
  } catch {
    // already closed
  }
}
