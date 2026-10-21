"""Raw relay volume probe: no app protocol, no OPFS.

Two browser pages join one relay room. The sender pushes N MiB of opaque
binary frames in bursts (default 8 x 256 KiB = 2 MiB, the v1.2 burst cap);
the receiver only counts bytes. Neither side implements ACK/retransmit.

Purpose: isolate "single WebSocket connection data volume" from the app.
If this probe survives far beyond 100 MiB, the 1006 is not a per-connection
byte cap; if it reproducibly dies at a fixed volume, it is.

Usage:
  python probe-volume.py [BASE] [TOTAL_MIB] [CHUNK_KIB] [BURST] [GAP_MS]
"""

import json
import os
import secrets
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://relay-min.fvlb2n3o6i.workers.dev"
TOTAL_MIB = int(sys.argv[2]) if len(sys.argv) > 2 else 100
CHUNK_KIB = int(sys.argv[3]) if len(sys.argv) > 3 else 256
BURST = int(sys.argv[4]) if len(sys.argv) > 4 else 8
GAP_MS = int(sys.argv[5]) if len(sys.argv) > 5 else 30
# "direct" launches the browser with --no-proxy-server to bypass the system
# proxy (Clash). Used to tell a proxy-path stall apart from a Cloudflare one.
BYPASS_PROXY = len(sys.argv) > 6 and sys.argv[6] == "direct"

ROOM = secrets.token_urlsafe(16)
print(
    f"INFO room={ROOM} total={TOTAL_MIB}MiB chunk={CHUNK_KIB}KiB burst={BURST} "
    f"gap={GAP_MS}ms proxy={'bypass' if BYPASS_PROXY else 'system'}",
    flush=True,
)

SENDER_JS = """(args) => {
  const { room, total, chunk, burst, gap } = args;
  const wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host +
    '/ws/' + encodeURIComponent(room) +
    '?clientId=probe-sender&connId=' + crypto.randomUUID() + '&v=1';
  const probe = { role: 'sender', opened: false, sent: 0, closed: null, doneAt: null,
                  startedAt: null, errors: 0 };
  window.__probe = probe;
  window.__probeDone = new Promise((resolve) => { probe._resolve = resolve; });
  const ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';
  // Random payload: no compression skew, no accidental structure.
  const frame = new Uint8Array(chunk * 1024);
  for (let i = 0; i < frame.length; i += 65536) {
    crypto.getRandomValues(frame.subarray(i, Math.min(i + 65536, frame.length)));
  }
  let settled = false;
  const settle = () => { if (!settled) { settled = true; probe._resolve(); } };
  ws.onopen = () => {
    probe.opened = true;
    probe.startedAt = Date.now();
    const totalBytes = total * 1024 * 1024;
    let sent = 0;
    // Pace to network drain rate via bufferedAmount so the browser does not
    // hoard hundreds of MiB in its own send buffer.
    const step = () => {
      if (ws.readyState !== WebSocket.OPEN) return;
      if (sent >= totalBytes) {
        if (ws.bufferedAmount === 0) {
          probe.doneAt = Date.now();
          settle();
          return;
        }
        setTimeout(step, 25);
        return;
      }
      if (ws.bufferedAmount > 2 * 1024 * 1024) {
        setTimeout(step, 20);
        return;
      }
      for (let i = 0; i < burst && sent < totalBytes; i++) {
        ws.send(frame);
        sent += frame.byteLength;
        probe.sent = sent;
      }
      setTimeout(step, gap);
    };
    step();
  };
  ws.onclose = (e) => {
    probe.closed = { code: e.code, reason: e.reason, wasClean: e.wasClean,
                     at: Date.now(), sentAtClose: probe.sent };
    settle();
  };
  ws.onerror = () => { probe.errors += 1; };
}
"""

RECEIVER_JS = """(room) => {
  const wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host +
    '/ws/' + encodeURIComponent(room) +
    '?clientId=probe-receiver&connId=' + crypto.randomUUID() + '&v=1';
  const probe = { role: 'receiver', opened: false, bytes: 0, frames: 0,
                  closed: null, firstAt: null, lastAt: null, errors: 0 };
  window.__probe = probe;
  const ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => { probe.opened = true; };
  ws.onmessage = (e) => {
    if (typeof e.data === 'string') return;
    if (!probe.firstAt) probe.firstAt = Date.now();
    probe.bytes += e.data.byteLength;
    probe.frames += 1;
    probe.lastAt = Date.now();
  };
  ws.onclose = (e) => {
    probe.closed = { code: e.code, reason: e.reason, wasClean: e.wasClean, at: Date.now() };
  };
  ws.onerror = () => { probe.errors += 1; };
}
"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel=os.environ.get("RELAY_BROWSER", "msedge"),
            headless=True,
            args=["--no-proxy-server"] if BYPASS_PROXY else [],
        )
        ctx_s = browser.new_context()
        ctx_r = browser.new_context()
        sender = ctx_s.new_page()
        receiver = ctx_r.new_page()

        for name, page in (("S", sender), ("R", receiver)):
            page.on("pageerror", lambda err, n=name: print(f"{n} PAGEERROR {err}", flush=True))

        sender.goto(f"{BASE}/", wait_until="domcontentloaded")
        receiver.goto(f"{BASE}/", wait_until="domcontentloaded")

        receiver.evaluate(RECEIVER_JS, ROOM)
        # Give the receiver a moment to join so the sender's first bursts are
        # not dropped for lack of a peer.
        time.sleep(1.0)
        sender.evaluate(
            SENDER_JS,
            {"room": ROOM, "total": TOTAL_MIB, "chunk": CHUNK_KIB,
             "burst": BURST, "gap": GAP_MS},
        )
        print("INFO sender started", flush=True)

        started = time.time()
        deadline = started + 1800
        s_state = r_state = None
        while time.time() < deadline:
            s_state = sender.evaluate("() => window.__probe")
            r_state = receiver.evaluate("() => window.__probe")
            if s_state.get("closed") or r_state.get("closed"):
                break
            if s_state.get("doneAt") and r_state.get("bytes", 0) >= TOTAL_MIB * 1024 * 1024:
                break
            time.sleep(0.5)

        # Watch for delayed closes for a few seconds after completion.
        for _ in range(10):
            if s_state is None or r_state is None:
                break
            if s_state.get("closed") or r_state.get("closed"):
                break
            time.sleep(0.5)
            s_state = sender.evaluate("() => window.__probe")
            r_state = receiver.evaluate("() => window.__probe")

        s_state = sender.evaluate("() => window.__probe")
        r_state = receiver.evaluate("() => window.__probe")
        elapsed = time.time() - started

        print(f"S {json.dumps(s_state, sort_keys=True)}", flush=True)
        print(f"R {json.dumps(r_state, sort_keys=True)}", flush=True)
        print(f"INFO elapsed={elapsed:.1f}s", flush=True)

        ok = (
            not s_state.get("closed")
            and not r_state.get("closed")
            and s_state.get("sent", 0) >= TOTAL_MIB * 1024 * 1024
        )
        print(
            f"INFO delivered={r_state.get('bytes', 0)}B "
            f"({r_state.get('bytes', 0) / 1024 / 1024:.1f} MiB, {r_state.get('frames', 0)} frames)",
            flush=True,
        )
        print("PASS no close during volume probe" if ok else "FAIL close observed", flush=True)

        browser.close()


if __name__ == "__main__":
    main()

