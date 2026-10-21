"""Regression: an unauthenticated socket must not displace the peer.

A and B are fully connected. B leaves, then C connects with a fresh clientId
but never sends hello. A must keep B's connId and must not log a new join.
C then sends a non-hello first frame and must be closed with 4002.

Usage: python phase5-unauth-presence.py [BASE]
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

STATE_JS = """() => ({
  ready: window.__relay.state.peerReady,
  peer: {
    clientId: window.__relay.state.peer.clientId,
    connId: window.__relay.state.peer.connId,
  },
  joinLogs: window.__relay.logs().filter((line) => line.includes("对端加入")).length,
  recv: window.__relay.getTransfers().filter((t) => t.direction === "recv").length,
})"""

OPEN_RAW_JS = """
(args) => {
  window.__c = new WebSocket(
    `ws://${location.host}/ws/${args.room}?clientId=${args.clientId}&connId=${args.connId}&v=1`
  );
  window.__cCode = null;
  window.__c.onclose = (e) => { window.__cCode = e.code; };
}
"""

SEND_PROBE_JS = """
() => {
  window.__c.send(JSON.stringify({
    v: 1, clientId: "c-unauth", connId: "c1", type: "probe", sentAt: Date.now(),
  }));
}
"""

tmp_a = tempfile.mkdtemp(prefix="relay-up-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-up-b-")
with sync_playwright() as p:
    ctx_a = p.chromium.launch_persistent_context(tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    ctx_b = p.chromium.launch_persistent_context(tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()
    c = ctx_a.new_page()

    errors = []
    for name, page in (("A", a), ("B", b), ("C", c)):
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

    # B goes offline; A keeps B's connId in state but is not ready.
    b.goto("about:blank")
    a.wait_for_function(
        "() => window.__relay.state.peerReady === false", timeout=15000
    )
    offline = a.evaluate(STATE_JS)
    print(f"INFO A offline state: {offline}", flush=True)

    # C connects without hello. It must not be announced to A.
    c.goto(BASE + "/", wait_until="domcontentloaded")
    c.wait_for_function("() => Boolean(window.__relay)", timeout=20000)
    c.evaluate(OPEN_RAW_JS, {"room": room, "clientId": "c-unauth", "connId": "c1"})
    c.wait_for_function("() => window.__c && window.__c.readyState === 1", timeout=10000)
    a.wait_for_timeout(1200)
    after_join = a.evaluate(STATE_JS)

    if after_join["peer"]["connId"] != offline["peer"]["connId"]:
        raise AssertionError(f"unauth socket displaced peer: {offline} -> {after_join}")
    if after_join["joinLogs"] != offline["joinLogs"]:
        raise AssertionError(f"A logged a join for unauth socket: {after_join}")
    if after_join["recv"] != 0:
        raise AssertionError(f"unexpected receive transfer: {after_join}")
    print("PASS unauthenticated socket did not displace peer", flush=True)

    # A non-hello first frame must be rejected with 4002.
    c.evaluate(SEND_PROBE_JS)
    c.wait_for_function("() => window.__cCode !== null", timeout=10000)
    code = c.evaluate("() => window.__cCode")
    if code != 4002:
        raise AssertionError(f"expected close 4002, got {code}")
    print("PASS non-hello first frame closed with 4002", flush=True)

    if errors:
        raise AssertionError(f"page errors: {errors}")
    print("PASS no page errors", flush=True)

    ctx_a.close()
    ctx_b.close()

