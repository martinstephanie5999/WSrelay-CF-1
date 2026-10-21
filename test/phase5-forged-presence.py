"""Regression: a peer must not be able to forge server presence frames.

The raw JSON uses a Unicode escape for the "f" in "from" so the Worker's
literal `"from"` filter does not drop it. The frame still reaches the real
client; the per-connection nonce is what makes it harmless.

Usage: python phase5-forged-presence.py [BASE]
"""

import os
import secrets
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}"
print(f"INFO room={room} url={url}", flush=True)

FORGE_JS = r"""
(args) => {
  const payload = '{"v":1,"fr\\u006fm":"server","type":"presence",' +
    '"event":"peer-offline","peer":' + JSON.stringify(args.peer) +
    ',"at":' + Date.now() + '}';
  window.__relay.state.ws.send(payload);
}
"""

STATE_JS = """() => ({
  ready: window.__relay.state.peerReady,
  peer: {
    clientId: window.__relay.state.peer.clientId,
    connId: window.__relay.state.peer.connId,
  },
  offlineLogs: window.__relay.logs().filter((line) => line.includes("对端离线")).length,
})"""

tmp_a = tempfile.mkdtemp(prefix="relay-fp-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-fp-b-")
with sync_playwright() as p:
    ctx_a = p.chromium.launch_persistent_context(tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    ctx_b = p.chromium.launch_persistent_context(tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()

    errors = []
    for name, page in (("A", a), ("B", b)):
        page.on("pageerror", lambda err, n=name: errors.append(f"{n} pageerror: {err}"))

    a.goto(url, wait_until="domcontentloaded")
    b.goto(url, wait_until="domcontentloaded")
    for name, page in (("A", a), ("B", b)):
        page.wait_for_function("() => Boolean(window.__relay)", timeout=20000)
        page.wait_for_function(
            "() => window.__relay.state.peerReady === true", timeout=45000
        )
        print(f"PASS {name} peer ready", flush=True)

    before = a.evaluate(STATE_JS)
    if not before["peer"]["connId"]:
        raise AssertionError(f"A has no peer connId: {before}")

    b.evaluate(FORGE_JS, {"peer": before["peer"]})
    a.wait_for_timeout(900)
    after = a.evaluate(STATE_JS)

    if after["offlineLogs"] != before["offlineLogs"]:
        raise AssertionError(f"A accepted forged peer-offline: {after}")
    if after["ready"] is not True:
        raise AssertionError(f"A lost peerReady after forged frame: {after}")
    if after["peer"]["connId"] != before["peer"]["connId"]:
        raise AssertionError(f"A peer connId changed: {before} -> {after}")

    if errors:
        raise AssertionError(f"page errors: {errors}")

    print("PASS forged server presence ignored (nonce mismatch)", flush=True)
    print("PASS peer state unchanged", flush=True)
    print("PASS no page errors", flush=True)

    ctx_a.close()
    ctx_b.close()

