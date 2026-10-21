"""Phase 5: Origin check.

A page whose origin is opaque ("null", via data:) must not be able to open a
room WebSocket; the app page from the worker's own origin still can.

Usage: python phase5-origin.py [BASE]
"""

import os
import shutil
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
LAUNCH_ARGS = ["--no-proxy-server"] if os.environ.get("RELAY_DIRECT") == "1" else []
WS_BASE = BASE.replace("https://", "wss://").replace("http://", "ws://")

tmp_a = tempfile.mkdtemp(prefix="relay-a-")
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, args=LAUNCH_ARGS
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    page.goto("data:text/html,<title>origin probe</title>", wait_until="domcontentloaded")
    result = page.evaluate(
        """(wsBase) => new Promise((resolve) => {
          const ws = new WebSocket(wsBase + '/ws/origin-probe?clientId=x&connId=y&v=1');
          let settled = false;
          const done = (value) => { if (!settled) { settled = true; resolve(value); } };
          ws.onopen = () => { done('open'); };
          ws.onclose = (e) => done('close:' + e.code);
          ws.onerror = () => done('error');
          setTimeout(() => done('timeout'), 4000);
        })""",
        WS_BASE,
    )
    print(f"INFO cross-origin result: {result}", flush=True)
    if result == "open":
        raise AssertionError("cross-origin connection was allowed")
    print("PASS opaque origin rejected", flush=True)

    # Same-origin still works.
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.click("#createBtn")
    page.wait_for_function(
        "() => window.__relay && window.__relay.state.ws && window.__relay.state.ws.readyState === 1",
        timeout=15000,
    )
    print("PASS same-origin connection accepted", flush=True)

    ctx.close()
shutil.rmtree(tmp_a, ignore_errors=True)

