import CLIENT_HTML from "./client.html";
import QRCODE_SRC from "./qrcode.txt";
import FAVICON_SVG from "./favicon.txt";
import FAVICON_32 from "./favicon-32.png";
import FAVICON_180 from "./favicon-180.png";
import FONT_WOFF2 from "./fonts/jetbrains-mono-latin-400.woff2";
import {
  PROTOCOL_VERSION,
  MAGIC_0,
  MAGIC_1,
  HEADER_SIZE,
  FLAG_FINAL,
  CHUNK_SIZE,
  WINDOW_SIZE,
  PERSIST_EVERY_CHUNKS,
  MIN_CHUNK_SIZE,
  MAX_CHUNK_SIZE,
  MAX_WINDOW_SIZE,
  CONTROL_MAX_BYTES,
  EARLY_DATA_MAX_BYTES,
  CLOSE_REPLACED,
  CLOSE_PROTOCOL_VERSION,
  CLOSE_ROOM_FULL,
  CLOSE_ROOM_EXPIRED,
  CLOSE_WRONG_PASSWORD,
  ROOM_TTL_MS,
} from "./protocol.js";

const ROOM_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;

const PROTOCOL_MODULE = `
export const PROTOCOL_VERSION = ${PROTOCOL_VERSION};
export const MAGIC_0 = ${MAGIC_0};
export const MAGIC_1 = ${MAGIC_1};
export const HEADER_SIZE = ${HEADER_SIZE};
export const FLAG_FINAL = ${FLAG_FINAL};
export const CHUNK_SIZE = ${CHUNK_SIZE};
export const WINDOW_SIZE = ${WINDOW_SIZE};
export const PERSIST_EVERY_CHUNKS = ${PERSIST_EVERY_CHUNKS};
export const MIN_CHUNK_SIZE = ${MIN_CHUNK_SIZE};
export const MAX_CHUNK_SIZE = ${MAX_CHUNK_SIZE};
export const MAX_WINDOW_SIZE = ${MAX_WINDOW_SIZE};
export const CONTROL_MAX_BYTES = ${CONTROL_MAX_BYTES};
export const EARLY_DATA_MAX_BYTES = ${EARLY_DATA_MAX_BYTES};
`;

function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const upgrade = request.headers.get("Upgrade");

    if (upgrade && upgrade.toLowerCase() === "websocket") {
      // Debug headers are internal-only. Reject external attempts outright
      // instead of forwarding them to the Durable Object.
      if (request.headers.has("x-debug") || request.headers.has("x-debug-token")) {
        return new Response("not found", { status: 404 });
      }
      const match = url.pathname.match(/^\/ws\/([^/]+)$/);
      if (!match) return new Response("not found", { status: 404 });
      const roomId = safeDecode(match[1]);
      if (!roomId || roomId.length > 256) {
        return new Response("not found", { status: 404 });
      }
      const id = env.RELAY_ROOM.idFromName(roomId);
      // Debug headers are internal-only. Strip anything an external client
      // sends before the request reaches the Durable Object.
      const headers = new Headers(request.headers);
      headers.delete("x-debug");
      headers.delete("x-debug-token");
      return env.RELAY_ROOM.get(id).fetch(new Request(request, { headers }));
    }

    if (url.pathname === "/fonts/jetbrains-mono.woff2") {
      // Numeric glyphs get a real mono font instead of whatever the OS picks.
      return new Response(FONT_WOFF2, {
        headers: {
          "content-type": "font/woff2",
          "cache-control": "public, max-age=31536000, immutable",
        },
      });
    }

    if (url.pathname === "/qrcode.js") {
      // Vendored qrcode-generator (MIT, Kazuhiko Arase); served as a classic
      // script so the page can render share-link QR codes without a CDN.
      return new Response(QRCODE_SRC, {
        headers: {
          "content-type": "text/javascript; charset=utf-8",
          "cache-control": "public, max-age=86400",
        },
      });
    }

    if (url.pathname === "/protocol.js") {
      return new Response(PROTOCOL_MODULE, {
        headers: {
          "content-type": "text/javascript; charset=utf-8",
          "cache-control": "no-store",
        },
      });
    }

    if (url.pathname === "/" || url.pathname.startsWith("/r/")) {
      return new Response(CLIENT_HTML, {
        headers: {
          "content-type": "text/html; charset=utf-8",
          "cache-control": "no-store",
          "x-content-type-options": "nosniff",
          "x-frame-options": "DENY",
          "referrer-policy": "no-referrer",
          "permissions-policy": "camera=(), microphone=(), geolocation=()",
          "content-security-policy":
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self' ws: wss:; worker-src 'self' blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        },
      });
    }

    if (url.pathname.startsWith("/__debug/storage/")) {
      // Audit endpoint. Only available when DEBUG_TOKEN is configured; never
      // expose it in the normal production deployment.
      const token = request.headers.get("x-debug-token") || url.searchParams.get("token");
      if (!env.DEBUG_TOKEN || token !== env.DEBUG_TOKEN) {
        return new Response("not found", { status: 404 });
      }
      const roomId = safeDecode(url.pathname.slice("/__debug/storage/".length));
      if (!roomId || roomId.length > 256) {
        return new Response("not found", { status: 404 });
      }
      const id = env.RELAY_ROOM.idFromName(roomId);
      return env.RELAY_ROOM.get(id).fetch(
        new Request("https://relay.internal/debug", {
          headers: {
            "x-debug": "1",
            "x-debug-token": env.DEBUG_TOKEN,
          },
        }),
      );
    }

    if (url.pathname === "/favicon.svg") {
      // Brand mark: two nodes joined by a line, matching the channel motif.
      return new Response(FAVICON_SVG, {
        headers: {
          "content-type": "image/svg+xml",
          "cache-control": "public, max-age=86400",
        },
      });
    }

    if (url.pathname === "/favicon-32.png" || url.pathname === "/favicon-180.png") {
      return new Response(url.pathname.endsWith("180.png") ? FAVICON_180 : FAVICON_32, {
        headers: {
          "content-type": "image/png",
          "cache-control": "public, max-age=86400",
        },
      });
    }

    if (url.pathname === "/favicon.ico") {
      // Browsers that ignore the link tags still get the PNG.
      return new Response(FAVICON_32, {
        headers: {
          "content-type": "image/png",
          "cache-control": "public, max-age=86400",
        },
      });
    }

    return new Response("not found", { status: 404 });
  },
};

export class RelayRoom {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    // Password checks do read-modify-write on the same meta key. Serialize
    // them per DO instance so concurrent hellos cannot lose failure counts.
    this.authChain = Promise.resolve();
    // Runs on first load and after every hibernation wake. Used during the
    // 1006 investigation to tell DO restarts apart from client-side drops.
    console.log("relay-do-init", new Date().toISOString());
  }

  async fetch(request) {
    const url = new URL(request.url);

    // Internal audit path only: the Worker strips these headers from external
    // upgrade requests, and the token must match the deployed DEBUG_TOKEN.
    if (
      request.headers.get("x-debug") === "1" &&
      this.env.DEBUG_TOKEN &&
      request.headers.get("x-debug-token") === this.env.DEBUG_TOKEN
    ) {
      const storageKeys = [...(await this.ctx.storage.list()).keys()];
      const sockets = this.ctx.getWebSockets().map((ws) => {
        const a = ws.deserializeAttachment() || {};
        return {
          clientId: a.clientId,
          connId: a.connId,
          attachmentKeys: Object.keys(a),
        };
      });
      return Response.json({ storageKeys, sockets });
    }

    const upgrade = request.headers.get("Upgrade");
    if (!upgrade || upgrade.toLowerCase() !== "websocket") {
      return new Response("expected websocket upgrade", { status: 426 });
    }

    // Same-origin only. Non-browser clients send no Origin and stay allowed;
    // opaque origins ("null") and foreign hosts are rejected outright.
    const origin = request.headers.get("Origin");
    if (origin !== null) {
      let originHost = null;
      try {
        originHost = new URL(origin).host;
      } catch {
        originHost = null;
      }
      if (originHost !== url.host) {
        return new Response("forbidden origin", { status: 403 });
      }
    }

    const clientId = url.searchParams.get("clientId") || "";
    const connId = url.searchParams.get("connId") || "";
    const protocol = url.searchParams.get("v") || "";
    const roomMatch = url.pathname.match(/^\/ws\/([^/]+)$/);
    const roomId = roomMatch ? safeDecode(roomMatch[1]) : null;
    const rawProtocolHeader = request.headers.get("sec-websocket-protocol") || "";
    const protocolHeader =
      rawProtocolHeader.length <= EARLY_DATA_MAX_BYTES ? rawProtocolHeader : "";
    const offeredProtocols = protocolHeader
      .split(",")
      .map((token) => token.trim())
      .filter(Boolean);
    // The browser client offers "relay.v1.<nonce>". The nonce is echoed back
    // in the 101 subprotocol and tagged onto every server-generated frame, so
    // a peer cannot forge presence for this connection.
    const relayProtocol =
      offeredProtocols.find(
        (token) => token.startsWith("relay.v1.") && token.length <= 128,
      ) || "";
    const serverNonce = relayProtocol ? relayProtocol.slice("relay.v1.".length) : null;
    const earlyData = relayProtocol ? "" : offeredProtocols[0] || "";

    if (
      !roomId ||
      roomId.length > 256 ||
      clientId.length > 128 ||
      connId.length > 128 ||
      protocol !== String(PROTOCOL_VERSION) ||
      !clientId ||
      !connId
    ) {
      // Accept first so the client receives a proper close code rather than a
      // generic handshake failure that would trigger a reconnect loop.
      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair);
      this.ctx.acceptWebSocket(server);
      server.serializeAttachment({
        roomId,
        clientId,
        connId,
        joinedAt: Date.now(),
        earlyData: null,
        serverNonce,
        rejected: true,
        greeted: true,
        announced: true,
        authChecked: true,
      });
      // Close after the 101 has reached the client. Calling close() before
      // returning 101 can surface as a handshake failure (1006), which makes
      // the client retry instead of honoring the terminal close code.
      setTimeout(() => {
        try {
          server.close(CLOSE_PROTOCOL_VERSION, "unsupported_protocol");
        } catch {
          // Already closed.
        }
      }, 0);
      const headers = {};
      if (protocolHeader) {
        headers["Sec-WebSocket-Protocol"] = relayProtocol || offeredProtocols[0];
      }
      return new Response(null, { status: 101, webSocket: client, headers });
    }

    const existing = this.ctx.getWebSockets();

    // A new connection with the same clientId replaces the old one.
    const peers = [];
    for (const ws of existing) {
      const a = ws.deserializeAttachment() || {};
      if (a.clientId === clientId) {
        try {
          ws.close(CLOSE_REPLACED, "replaced");
        } catch {
          // already gone
        }
      } else {
        peers.push(ws);
      }
    }

    // One-to-one room: a third distinct client is rejected with a close code.
    if (peers.length >= 2) {
      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair);
      this.ctx.acceptWebSocket(server);
      server.serializeAttachment({
        roomId,
        clientId,
        connId,
        joinedAt: Date.now(),
        earlyData: null,
        serverNonce,
        rejected: true,
        greeted: true,
        announced: true,
        authChecked: true,
      });
      // See the protocol-version rejection above: let the 101 land first so
      // the browser receives 4003 instead of a 1006 handshake failure.
      setTimeout(() => {
        try {
          server.close(CLOSE_ROOM_FULL, "room_full");
        } catch {
          // Already closed.
        }
      }, 0);
      const headers = {};
      if (protocolHeader) {
        headers["Sec-WebSocket-Protocol"] = relayProtocol || offeredProtocols[0];
      }
      return new Response(null, { status: 101, webSocket: client, headers });
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server);
    server.serializeAttachment({
      roomId,
      clientId,
      connId,
      joinedAt: Date.now(),
      earlyData: earlyData || null,
      serverNonce,
      rejected: false,
      greeted: false,
      announced: false,
      authChecked: false,
    });

    // Presence is the only server-generated application frame. It carries
    // connection metadata only; file bytes never touch this path. It is
    // emitted after the hello/auth gate (see webSocketMessage), not here, so
    // an unauthenticated socket cannot displace the established peer.

    const headers = {};
    if (protocolHeader) {
      // Browsers fail the handshake if an offered subprotocol is not echoed.
      headers["Sec-WebSocket-Protocol"] = relayProtocol || offeredProtocols[0];
    }

    return new Response(null, { status: 101, webSocket: client, headers });
  }

  async webSocketMessage(ws, message) {
    // Transparent relay: one frame in, one frame out, no buffering, no parsing.
    const peers = this.ctx.getWebSockets();
    const attachment = ws.deserializeAttachment() || {};
    let outbound = message;

    // Handshakes rejected for room-full / protocol-version must never relay.
    if (attachment.rejected) return;

    // Every connection must start with hello. No greeting and no forwarding
    // happens until the password check has actually passed; otherwise a
    // password room is only a UI hint (any non-hello first frame used to skip
    // the check permanently).
    if (!attachment.authed) {
      if (typeof message !== "string") {
        try {
          ws.close(CLOSE_PROTOCOL_VERSION, "hello_required");
        } catch {
          // Already closed.
        }
        return;
      }
      let hello = null;
      try {
        hello = JSON.parse(message);
      } catch {
        hello = null;
      }
      if (!hello || hello.type !== "hello") {
        try {
          ws.close(CLOSE_PROTOCOL_VERSION, "hello_required");
        } catch {
          // Already closed.
        }
        return;
      }
      const ok = await this.checkRoomPassword(ws, attachment.roomId, hello.auth);
      if (!ok) return; // closed with 4005; never forwarded to the peer
      // Never relay the password hash. The peer only needs the hello fields.
      if (Object.prototype.hasOwnProperty.call(hello, "auth")) {
        delete hello.auth;
        outbound = JSON.stringify(hello);
      }
      attachment.authed = true;
      attachment.authChecked = true;
      try {
        ws.serializeAttachment(attachment);
      } catch {
        // Socket already gone.
      }
    }

    // Older clients resend auth on every hello; strip it from any hello we
    // forward, not only the first one. Parse only frames that mention auth.
    if (typeof outbound === "string" && outbound.includes('"auth"')) {
      try {
        const parsed = JSON.parse(outbound);
        if (
          parsed &&
          parsed.type === "hello" &&
          Object.prototype.hasOwnProperty.call(parsed, "auth")
        ) {
          delete parsed.auth;
          outbound = JSON.stringify(parsed);
        }
      } catch {
        // Not JSON; forward as-is.
      }
    }

    // Clients must never emit the server's presence channel. Drop forged
    // frames here so a peer cannot make its counterpart treat them as
    // authoritative server events.
    if (typeof outbound === "string" && outbound.includes('"from"')) {
      try {
        const parsed = JSON.parse(outbound);
        if (parsed && parsed.from === "server") {
          console.log("relay-drop-forged-server-frame");
          return;
        }
      } catch {
        // Not JSON; forward as-is.
      }
    }

    // Announce the connection to existing peers only after the hello/auth
    // gate. An unauthenticated socket must not displace the established peer.
    if (!attachment.announced) {
      attachment.announced = true;
      try {
        ws.serializeAttachment(attachment);
      } catch {
        // Socket already gone.
      }
      for (const peer of peers) {
        if (peer === ws) continue;
        const pa = peer.deserializeAttachment() || {};
        if (pa.rejected || !pa.authed) continue;
        try {
          peer.send(
            JSON.stringify({
              v: PROTOCOL_VERSION,
              from: "server",
              type: "presence",
              event: "peer-joined",
              nonce: pa.serverNonce || null,
              peer: {
                clientId: attachment.clientId,
                connId: attachment.connId,
                earlyData: attachment.earlyData || null,
              },
              at: Date.now(),
            }),
          );
        } catch {
          // Ignore a dead peer entry.
        }
      }
    }

    // A frame sent before the 101 return can be lost, so the newcomer is told
    // about existing peers on its first message instead. Both sides then know
    // each other's current connId after a simultaneous reconnect.
    if (!attachment.greeted) {
      attachment.greeted = true;
      try {
        ws.serializeAttachment(attachment);
      } catch {
        // Socket already gone; nothing to greet.
      }
      for (const peer of peers) {
        if (peer === ws) continue;
        const pa = peer.deserializeAttachment() || {};
        if (pa.rejected || !pa.authed) continue;
        try {
          ws.send(
            JSON.stringify({
              v: PROTOCOL_VERSION,
              from: "server",
              type: "presence",
              event: "peer-joined",
              nonce: attachment.serverNonce || null,
              peer: {
                clientId: pa.clientId,
                connId: pa.connId,
                earlyData: pa.earlyData || null,
              },
              at: Date.now(),
            }),
          );
        } catch {
          // Ignore a dead peer entry.
        }
      }
    }

    let forwarded = 0;
    for (const peer of peers) {
      if (peer === ws) continue;
      const pa = peer.deserializeAttachment() || {};
      if (pa.rejected || !pa.authed) continue;
      try {
        peer.send(outbound);
        forwarded++;
      } catch (err) {
        // Log only the transport error summary, never the payload.
        console.log("relay-forward-failed", String(err).slice(0, 120));
      }
    }

    // Metadata-only logging. Binary frames are logged only when they could
    // not be forwarded; per-frame logs at 4k frames/GiB are pure overhead.
    if (typeof outbound === "string" || forwarded === 0) {
      console.log(
        "relay-frame",
        typeof outbound === "string" ? "text" : "binary",
        typeof outbound === "string" ? outbound.length : outbound.byteLength,
        "peers",
        peers.length,
        "forwarded",
        forwarded,
      );
    }
  }

  checkRoomPassword(ws, roomId, auth) {
    const run = () => this.checkRoomPasswordUnlocked(ws, roomId, auth);
    const next = this.authChain.then(run, run);
    this.authChain = next.then(
      () => {},
      () => {},
    );
    return next;
  }

  async checkRoomPasswordUnlocked(ws, roomId, auth) {
    const key = `room:${roomId}:meta`;
    let meta = null;
    try {
      meta = await this.ctx.storage.get(key);
    } catch {
      meta = null;
    }
    const now = Date.now();
    const ttl = Number(this.env.ROOM_TTL_MS) || ROOM_TTL_MS;
    // Migrate metadata written before TTL existed, then expire the room.
    if (meta && meta.passwordHash && !meta.expiresAt) {
      meta.expiresAt = (Number(meta.createdAt) || now) + ttl;
      try {
        await this.ctx.storage.put(key, meta);
        await this.ctx.storage.setAlarm(meta.expiresAt);
      } catch {
        // TTL is best-effort if storage is unavailable.
      }
    }
    if (meta && meta.expiresAt && now >= meta.expiresAt) {
      for (const socket of this.ctx.getWebSockets()) {
        try {
          socket.close(CLOSE_ROOM_EXPIRED, "room_expired");
        } catch {
          // Already closed.
        }
      }
      try {
        await this.ctx.storage.delete(key);
      } catch {
        // Non-fatal.
      }
      return false;
    }
    const hash = typeof auth === "string" && /^[0-9a-f]{64}$/.test(auth) ? auth : "";
    if (meta && meta.passwordHash) {
      // Brute-force brake: after 8 failures in a 10-minute window the room
      // refuses every attempt until the block expires, then starts fresh.
      if (meta.blockedUntil && now < meta.blockedUntil) {
        try {
          ws.close(CLOSE_WRONG_PASSWORD, "too_many_attempts");
        } catch {
          // Already closed.
        }
        return false;
      }
      if (hash !== meta.passwordHash) {
        const windowExpired = !meta.failResetAt || now > meta.failResetAt;
        const fails = (windowExpired ? 0 : meta.fails || 0) + 1;
        const patch = {
          passwordHash: meta.passwordHash,
          createdAt: meta.createdAt,
          expiresAt: meta.expiresAt || now + ttl,
          fails,
          failResetAt: windowExpired ? now + 10 * 60 * 1000 : meta.failResetAt,
          blockedUntil: fails >= 8 ? now + 10 * 60 * 1000 : meta.blockedUntil || 0,
        };
        try {
          await this.ctx.storage.put(key, patch);
        } catch {
          // Keep the room usable even if metadata writes fail.
        }
        // Small linear delay blunts scripted guessing without affecting UX.
        await new Promise((resolve) => setTimeout(resolve, Math.min(1000, 100 * fails)));
        try {
          ws.close(CLOSE_WRONG_PASSWORD, fails >= 8 ? "too_many_attempts" : "wrong_password");
        } catch {
          // Already closed.
        }
        return false;
      }
      // Correct password: clear the failure counters.
      if (meta.fails || meta.blockedUntil) {
        try {
          await this.ctx.storage.put(key, {
            passwordHash: meta.passwordHash,
            createdAt: meta.createdAt,
            expiresAt: meta.expiresAt,
            fails: 0,
            failResetAt: 0,
            blockedUntil: 0,
          });
        } catch {
          // Non-fatal.
        }
      }
      return true;
    }
    // No password yet: only the earliest joined connection in the room may set
    // one, so a joiner cannot lock the creator out. Comparing joinedAt instead
    // of the socket count also handles two simultaneous first hellos: the
    // serialized auth chain lets the earliest one set the password once.
    if (hash) {
      const sockets = this.ctx.getWebSockets();
      const mine = ws.deserializeAttachment() || {};
      const earliest = sockets.reduce((min, socket) => {
        const a = socket.deserializeAttachment() || {};
        const joined = Number(a.joinedAt) || Number.MAX_SAFE_INTEGER;
        return Math.min(min, joined);
      }, Number.MAX_SAFE_INTEGER);
      if (Number(mine.joinedAt) <= earliest) {
        try {
          await this.ctx.storage.put(key, {
            passwordHash: hash,
            createdAt: now,
            expiresAt: now + ttl,
            fails: 0,
            failResetAt: 0,
            blockedUntil: 0,
          });
          await this.ctx.storage.setAlarm(now + ttl);
        } catch {
          // Storage failure must not break a public room.
        }
      }
    }
    return true;
  }

  async alarm() {
    const now = Date.now();
    const keys = [...(await this.ctx.storage.list({ prefix: "room:" })).keys()];
    for (const key of keys) {
      const meta = await this.ctx.storage.get(key);
      if (!meta || !meta.expiresAt) continue;
      if (now < meta.expiresAt) {
        await this.ctx.storage.setAlarm(meta.expiresAt);
        continue;
      }
      if (!meta.expired) {
        await this.ctx.storage.put(key, { ...meta, expired: true, expiredAt: now });
        for (const socket of this.ctx.getWebSockets()) {
          try {
            socket.close(CLOSE_ROOM_EXPIRED, "room_expired");
          } catch {
            // Already closed.
          }
        }
        await this.ctx.storage.setAlarm(now + ROOM_RETENTION_MS);
      } else if (!meta.expiredAt || now - meta.expiredAt >= ROOM_RETENTION_MS) {
        await this.ctx.storage.delete(key);
      }
    }
  }

  async webSocketClose(ws, code, reason, wasClean) {
    const a = ws.deserializeAttachment() || {};
    // Connection metadata only: close code, truncated reason, connection age
    // and peer count. Never payload or file metadata.
    console.log(
      "relay-close",
      JSON.stringify({
        code,
        reason: String(reason || "").slice(0, 64),
        wasClean,
        connId: a.connId || null,
        ageMs: a.joinedAt ? Date.now() - a.joinedAt : null,
        peers: this.ctx.getWebSockets().length,
      }),
    );
    try {
      // 1005/1006/1015 are reserved and cannot be sent on the wire; echoing
      // them would leave the close handshake unfinished until the client
      // times out. Complete it with 1000 instead.
      const echoCode = [1005, 1006, 1015].includes(code) ? 1000 : code;
      ws.close(echoCode, reason);
    } catch {
      // Already closed.
    }

    if (a.rejected || !a.authed) return;

    for (const peer of this.ctx.getWebSockets()) {
      if (peer === ws) continue;
      const pa = peer.deserializeAttachment() || {};
      if (pa.rejected || !pa.authed) continue;
      try {
        peer.send(
          JSON.stringify({
            v: PROTOCOL_VERSION,
            from: "server",
            type: "presence",
            event: "peer-offline",
            nonce: pa.serverNonce || null,
            peer: { clientId: a.clientId, connId: a.connId },
            at: Date.now(),
          }),
        );
      } catch {
        // Ignore.
      }
    }

  }

  async webSocketError(ws, error) {
    // Transport error summary only, never payload or file metadata.
    console.log("relay-ws-error", String(error).slice(0, 120));
  }
}
