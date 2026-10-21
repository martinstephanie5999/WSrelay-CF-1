"""Room-full regression with real browsers.

A and B occupy the 1:1 room. C opens the same link as a third distinct
client: it must receive close 4003, show the terminate state, and must not
auto-reconnect or displace A/B's peer identity.

Usage: python phase5-room-full.py [BASE]
"""

import os
import secrets
import shutil
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}"
print(f"INFO room={room} url={url}", flush=True)

STATE_JS = """() => ({
  stopped: window.__relay.state.stopped,
  stoppedCode: window.__relay.state.stoppedCode,
  ws: window.__relay.state.ws ? window.__relay.state.ws.readyState : null,
  peerReady: window.__relay.state.peerReady,
  peerClientId: window.__relay.state.peer.clientId,
  clientId: localStorage.getItem("relay.clientId"),
  connText: document.getElementById("connText").textContent,
})"""

tmp_a = tempfile.mkdtemp(prefix="relay-rf-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-rf-b-")
tmp_c = tempfile.mkdtemp(prefix="relay-rf-c-")
with sync_playwright() as p:
    ctx_a = p.chromium.launch_persistent_context(tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    ctx_b = p.chromium.launch_persistent_context(tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    ctx_c = p.chromium.launch_persistent_context(tmp_c, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()
    c = ctx_c.pages[0] if ctx_c.pages else ctx_c.new_page()

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

    before_a = a.evaluate(STATE_JS)
    before_b = b.evaluate(STATE_JS)
    print(f"INFO A peer={before_a['peerClientId']} B peer={before_b['peerClientId']}", flush=True)

    c.goto(url, wait_until="domcontentloaded")
    c.wait_for_function("() => Boolean(window.__relay)", timeout=20000)
    try:
        c.wait_for_function(
            "() => window.__relay.state.stoppedCode === 4003", timeout=20000
        )
    except Exception:
        print("DEBUG C state:", c.evaluate(STATE_JS), flush=True)
        print("DEBUG C logs:", c.evaluate("() => window.__relay.logs().slice(-20)"), flush=True)
        raise

    c1 = c.evaluate(STATE_JS)
    print(f"INFO C state: {c1}", flush=True)
    if c1["stopped"] is not True:
        raise AssertionError(f"C not marked stopped: {c1}")
    if c1["stoppedCode"] != 4003:
        raise AssertionError(f"C wrong close code: {c1}")
    if "4003" not in c1["connText"]:
        raise AssertionError(f"C UI did not show 4003: {c1['connText']}")

    # No auto-reconnect: state.ws must stay null.
    time.sleep(1.5)
    c2 = c.evaluate(STATE_JS)
    if c2["ws"] is not None:
        raise AssertionError(f"C auto-reconnected: {c2}")
    if c2["stoppedCode"] != 4003:
        raise AssertionError(f"C stoppedCode changed: {c2}")
    print("PASS C got 4003 and did not reconnect", flush=True)

    after_a = a.evaluate(STATE_JS)
    after_b = b.evaluate(STATE_JS)
    if after_a["peerReady"] is not True or after_b["peerReady"] is not True:
        raise AssertionError(f"A/B lost peerReady: {after_a} / {after_b}")
    if after_a["peerClientId"] == c2["clientId"] or after_b["peerClientId"] == c2["clientId"]:
        raise AssertionError(
            f"C displaced a peer: C={c2['clientId']} A={after_a['peerClientId']} B={after_b['peerClientId']}"
        )
    print("PASS A/B peer state unchanged", flush=True)

    if errors:
        raise AssertionError(f"page errors: {errors}")
    print("PASS no page errors", flush=True)

    ctx_a.close()
    ctx_b.close()
    ctx_c.close()
for d in (tmp_a, tmp_b, tmp_c):
    shutil.rmtree(d, ignore_errors=True)

