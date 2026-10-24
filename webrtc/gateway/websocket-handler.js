/**
 * secdogie Edge Gateway — Cloudflare Worker WebSocket TCP Proxy / Tunnel Relay
 *
 * Lightweight bidirectional data forwarder and latency probe using Cloudflare's
 * Global Network. Accepts WebSocket upgrades, optionally routes based on client
 * ASN / country (from request.cf), and transparently pipes the stream to a
 * configurable Target Origin (direct TCP via cloudflare:sockets or a
 * Cloudflare Tunnel public endpoint).
 *
 * No authentication secrets, no hardcoded credentials, no inspection of
 * application payload — pure transport relay. Targets must be supplied via
 * environment or query (validated).
 *
 * Routes:
 *   GET  /config          → JSON with Base64-encoded client metadata
 *   GET  /health          → plain text ok
 *   WS   /proxy?target=host:port   → full-duplex relay to TCP origin
 */

import { connect } from 'cloudflare:sockets';

// Preferred backend hints for specific ASes (latency / peering optimization).
// Values are example hostnames; override via env.PREFERRED_TARGETS or query.
const ASN_HINTS = Object.freeze({
  9808: { region: 'cn', note: 'China Mobile' },       // AS9808
  17408: { region: 'tw', note: 'AboveNet / Chief TW' }, // AS17408
});

const MAX_TARGET_LENGTH = 253;
const TARGET_PATTERN = /^[a-zA-Z0-9.-]+:[1-9][0-9]{0,4}$/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === '/health') {
      return new Response('ok\n', { status: 200 });
    }

    if (url.pathname === '/config' && request.method === 'GET') {
      return handleConfig(request, env);
    }

    if (url.pathname === '/proxy') {
      return handleProxy(request, env, url);
    }

    return new Response('not found\n', { status: 404 });
  },
};

/** GET /config — returns Base64-encoded client connection metadata. */
function handleConfig(request, env) {
  const cf = request.cf || {};
  const asn = Number(cf.asn) || 0;
  const meta = {
    asn,
    country: cf.country || null,
    colo: cf.colo || null,
    asOrganization: cf.asOrganization || null,
    connectingIp: request.headers.get('CF-Connecting-IP') || null,
    hint: ASN_HINTS[asn] || null,
    timestamp: Date.now(),
    // Suggested endpoints can be injected via env for multi-region setups.
    preferredTargets: (env.PREFERRED_TARGETS || '').split(',').filter(Boolean),
  };

  const encoded = btoa(JSON.stringify(meta));
  return Response.json(
    { version: 1, metadata: encoded },
    {
      headers: {
        'Cache-Control': 'no-store',
        'Access-Control-Allow-Origin': '*',
      },
    },
  );
}

/** WebSocket /proxy — bidirectional pipe to Target Origin. */
async function handleProxy(request, env, url) {
  if (request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') {
    return new Response('expected WebSocket upgrade\n', {
      status: 426,
      headers: { Upgrade: 'websocket' },
    });
  }

  const target = url.searchParams.get('target') || env.DEFAULT_TARGET || '';
  if (!TARGET_PATTERN.test(target) || target.length > MAX_TARGET_LENGTH) {
    return new Response('invalid or missing target (host:port)\n', { status: 400 });
  }

  const [host, portStr] = target.split(':');
  const port = Number(portStr);

  // Optional ASN-aware logging / future redirection point.
  const asn = Number(request.cf?.asn) || 0;
  if (ASN_HINTS[asn]) {
    console.log(`edge-gateway: ASN ${asn} (${ASN_HINTS[asn].note}) → ${target}`);
  }

  const { 0: client, 1: server } = new WebSocketPair();
  server.accept({ allowHalfOpen: true });

  // Non-blocking pipe; errors close both sides cleanly.
  pipeToOrigin(server, host, port).catch((err) => {
    console.error('edge-gateway: pipe failed', err);
    try { server.close(1011, 'origin unreachable'); } catch { /* already closed */ }
  });

  return new Response(null, { status: 101, webSocket: client });
}

/** Pipe WebSocket frames ↔ TCP socket created by connect(). */
async function pipeToOrigin(ws, host, port) {
  let socket;
  try {
    socket = connect({ hostname: host, port }, { secureTransport: 'off' });
    await socket.opened;
  } catch (err) {
    throw new Error(`connect failed: ${err.message}`);
  }

  const writer = socket.writable.getWriter();
  const reader = socket.readable.getReader();

  // WS → TCP
  ws.addEventListener('message', async (event) => {
    try {
      const data = event.data;
      const bytes = data instanceof ArrayBuffer ? new Uint8Array(data)
        : typeof data === 'string' ? new TextEncoder().encode(data)
        : new Uint8Array(await data.arrayBuffer());
      await writer.write(bytes);
    } catch {
      try { ws.close(1011, 'write error'); } catch { /* noop */ }
    }
  });

  ws.addEventListener('close', () => {
    try { writer.close(); } catch { /* noop */ }
    try { socket.close(); } catch { /* noop */ }
  });

  // TCP → WS
  (async () => {
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (value) ws.send(value);
      }
    } catch {
      /* origin closed or error */
    } finally {
      try { ws.close(1000, 'origin closed'); } catch { /* noop */ }
    }
  })();
}
