# webrtc: browser P2P DataChannel

Chinese: [README.zh.md](README.zh.md)

A front-end-only WebRTC peer-to-peer data transport module, plus a WebSocket signaling
gateway running on Cloudflare Workers. After two browser tabs (or two machines) exchange
`offer` / `answer` / `ice-candidate` through the gateway, data flows over a **direct,
DTLS-encrypted RTCDataChannel**; the gateway neither sees nor handles application data.

**Starts only on an explicit user action.** Importing the module opens no socket, creates no
`RTCPeerConnection` and starts no timer; `startPeerConnection()` rejects outright without a
user gesture (a click), so a page cannot silently go online at load time. Closing the page or
clicking "Close node" tears everything down; nothing stays resident in the background.

| File | Purpose |
| --- | --- |
| [`signaling/worker.js`](signaling/worker.js) | Signaling gateway: Worker entry point + `SignalingRoom` Durable Object |
| [`client/web_peer.js`](client/web_peer.js) | Browser ES module: negotiation, DataChannel, disconnect detection |
| [`index.html`](index.html) | Minimal test page: a "Start node" button + sending and receiving text + a log |
| [`wrangler.toml`](wrangler.toml) | Worker / Durable Object configuration |
| [`tests/worker.test.js`](tests/worker.test.js) | Headless unit tests for the gateway (fake sockets + fake clock, plain Node) |

## Running locally (two tabs exchanging messages)

Requires Node 22+ (what the current wrangler requires) and Python 3 (only to serve static
files).

```sh
cd webrtc
npx wrangler dev                 # terminal 1: signaling gateway -> ws://localhost:8787/ws
python3 -m http.server 8080      # terminal 2: test page         -> http://localhost:8080/
```

1. Open <http://localhost:8080/> in two tabs with the same room name (default `demo`).
2. In tab A click "Start node" → state `waiting`; in tab B click "Start node" → both sides
   reach `connected`.
3. Send text from either side; the other side's log shows `← …`.

The test page's signaling URL and room can also be prefilled with query parameters:
`index.html?signal=wss://<worker>.workers.dev/ws&room=my-room`. ES modules cannot load from
`file://`, so the test page must be opened over HTTP (`localhost` counts as a secure context,
where WebRTC is available).

## Deploying the gateway

```sh
cd webrtc
npx wrangler deploy              # -> wss://secdogie-signaling.<account>.workers.dev/ws
```

Before deploying, set `ALLOWED_ORIGINS` in `wrangler.toml` to the test page's origin(s)
(comma-separated, e.g. `https://peer.example.com`); leaving it empty allows any origin and is
suitable only for local development. The Durable Object uses the SQLite-backed class, which
works on the free plan.

## Architecture

```
Tab A ──ws──┐                         ┌──ws── Tab B
            ├─ Worker ─ SignalingRoom ┤        (forwards only offer / answer / ice-candidate)
            │   (DO selected by room)  │
Tab A ◀═════════ RTCDataChannel (DTLS/SCTP, reliable + ordered) ═════════▶ Tab B
```

**Why a Durable Object.** A plain Worker may place the two WebSockets of one room in
different isolates, where a module-level `Map` is not shared, and routing would silently fail
in production. `idFromName(room)` pins every connection of a room to the same object, which
holds the in-memory "peer ID → active WebSocket" map. Nothing is written to storage except
the alarm used for the idle sweep.

**Roles and negotiation.** A room holds at most two peers. The peer that joins second receives
the other's ID in `welcome.peers` and acts as the offerer: `createDataChannel` →
`createOffer` → `setLocalDescription` → send `offer`. The first peer, on receiving the
`offer`, does `setRemoteDescription` → `createAnswer` → `setLocalDescription` → send
`answer`. ICE candidates are trickled; candidates gathered before the local description is
sent are queued, so the peer always receives the description first. Only the offerer
renegotiates, so both sides never offer at once (glare); every received `offer` is answered on
a fresh `RTCPeerConnection`.

**DataChannel.** `createDataChannel('secdogie-data', { ordered: true })` with neither
`maxRetransmits` nor `maxPacketLifeTime` set, i.e. reliable, ordered mode.

**Disconnect detection (`oniceconnectionstatechange`).**

| ICE state | Handling |
| --- | --- |
| `disconnected` | Enter `disconnected` with an 8 s self-healing grace period; if the gateway has already announced that the peer left, declare the link down immediately |
| `connected` / `completed` | Recovery within the grace period returns to `connected` |
| `failed`, or the grace period expires | Tear down the current link → `waiting`; if the peer is still in the room, the offerer re-offers up to 3 times with 1 s / 2 s / 4 s backoff |

In addition: a DataChannel that does not open within 30 s of negotiation counts as a failure
(retried the same way); a DataChannel `close` also counts as link loss; if signaling drops
while the direct link is up, the direct link is kept, otherwise the whole session enters
`failed`.

## Client API (`client/web_peer.js`)

```js
import {
  PeerState, startPeerConnection, closePeerConnection, sendData, onMessage, onStateChange,
} from './client/web_peer.js';

onStateChange((state, detail) => console.log(state, detail));
onMessage((data) => console.log('received', data));    // string or ArrayBuffer

button.onclick = async () => {                          // must be called within a user gesture
  const { peerId } = await startPeerConnection({
    signalingUrl: 'ws://localhost:8787/ws',
    room: 'demo',
    // iceServers: [...],   default stun:stun.l.google.com:19302; add TURN to traverse symmetric NAT
    // log: (line) => ...,  diagnostic log, console.debug by default
  });
};

await sendData('hello');            // string / ArrayBuffer / TypedArray / Blob sent as is; other values sent as JSON text
closePeerConnection();              // idempotent, callable at any time
```

| Function | Description |
| --- | --- |
| `startPeerConnection(opts)` | Connects to the gateway and joins the room; resolves `{peerId, room}` once the gateway admits it. Rejects without a user gesture, when already running, or on an invalid URL / room name |
| `closePeerConnection()` | Closes the DataChannel, the `RTCPeerConnection` and the signaling socket; state returns to `idle` |
| `sendData(payload)` | Sends over the DataChannel; rejects when the channel is not open; throws `RangeError` above the SCTP per-message limit; waits for the buffer to drain when more than 1 MiB is buffered |
| `onMessage(cb)` / `onStateChange(cb)` | Register a listener; return an unsubscribe function |

States: `idle` → `signaling` → `waiting` → `negotiating` → `connected`, plus
`disconnected` (ICE temporarily lost) and `failed` (gave up; call `startPeerConnection()`
again).

## Signaling protocol and gateway limits

Every frame is JSON text. The gateway forwards only three types; `from` is stamped by the
gateway from the sender's session (a client-supplied `from` is ignored), and `payload` is
rebuilt from an allowlist of `RTCSessionDescriptionInit` / `RTCIceCandidateInit` fields, with
any extra fields dropped.

| Direction | Messages |
| --- | --- |
| Client → gateway | `{type: 'offer' \| 'answer' \| 'ice-candidate', to, payload}`; `{type: 'ping'}` (heartbeat, not forwarded) |
| Gateway → client | `welcome {peerId, peers}`, `peer-joined {peerId}`, `peer-left {peerId}`, forwarded `{type, from, payload}`, `error {code, message}` |

| Limit | Value | When exceeded |
| --- | --- | --- |
| JSON frame size (UTF-8) | 16 KiB | Closed, 1009 |
| Rate (token bucket) | Burst 40 frames, sustained 10 frames/s | Closed, 1008 |
| Binary frames | Not accepted | Closed, 1003 |
| Peers per room | 2 | A third connection receives `room-full` and is closed, 4001 |
| Idle (no frame at all, heartbeats included) | 60 s (clients ping every 20 s) | Alarm sweeps every 30 s, closed, 4002 |
| Invalid JSON / type / payload / target | — | `error` returned, not forwarded |
| Room name | `[A-Za-z0-9_-]{1,64}` | HTTP 400 |
| Origin | Checked when `ALLOWED_ORIGINS` is non-empty | HTTP 403 |

## Tests

```sh
cd webrtc && npm test            # = node --test, no dependencies to install
```

The unit tests drive `SignalingRoom` directly with fake sockets and a fake clock: welcome and
join notifications, targeted forwarding of the three types and `from` stamping, the field
allowlist, invalid types / payloads / JSON / targets, oversized frames (including multi-byte
characters), binary frames, rate limiting, full rooms, disconnect notifications, the idle
sweep, and the entry point's health / upgrade / origin / room-name checks. CI (`webrtc-tests`
in `.github/workflows/test.yml`) runs them on every push.

The browser-side end-to-end path (a real `wrangler dev` + two Chromium tabs) is verified
manually following "Running locally" above.

## Known limitations

- **The room name is the only barrier.** The gateway does not authenticate identities; anyone
  who knows the room name can take a slot (first come, first served, refused once two peers
  are present). For public deployment, use hard-to-guess random room names or put Cloudflare
  Access in front of the Worker.
- **Peer identity depends on honest signaling.** DTLS keeps the link confidential, but the
  certificate fingerprints are exchanged through the gateway; a malicious signaling server
  could act as a man in the middle. When end-to-end identity is needed, add a DID-based
  authentication layer on top of the DataChannel (see `identity/`).
- **No default TURN.** Only public STUN is configured by default; when both sides are behind
  symmetric NAT the connection may fail. Pass your own TURN through `iceServers` when needed.
- **One-to-one.** A room pairs exactly two peers; multi-party use needs multiple rooms or a
  mesh extension.
- A single message is bounded by the SCTP limit (typically 256 KiB in Chrome); callers must
  fragment large files themselves.
