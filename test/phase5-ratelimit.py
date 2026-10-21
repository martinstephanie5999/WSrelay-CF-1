"""Phase 5: password brute-force brake.

After 8 wrong passwords in the window, every further attempt (even the
correct one) is refused until the block expires. The creator's live
connection must keep working.

Usage: python phase5-ratelimit.py [BASE]
"""

import os
import secrets
import shutil
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
LAUNCH_ARGS = ["--no-proxy-server"] if os.environ.get("RELAY_DIRECT") == "1" else []
PASSWORD = "right-" + secrets.token_hex(4)
print(f"INFO password={PASSWORD}", flush=True)

tmp_a = tempfile.mkdtemp(prefix="relay-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-b-")
with sync_playwright() as p:
    ctx_a = p.chromium.launch_persistent_context(
        tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, args=LAUNCH_ARGS
    )
    ctx_b = p.chromium.launch_persistent_context(
        tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, args=LAUNCH_ARGS
    )
    a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()

    a.goto(BASE + "/", wait_until="domcontentloaded")
    a.fill("#passwordInput", PASSWORD)
    a.click("#createBtn")
    a.wait_for_function("() => Boolean(window.__relay && window.__relay.state.roomId)", timeout=15000)
    room = a.evaluate("() => window.__relay.state.roomId")
    a.wait_for_function("() => window.__relay.state.ws && window.__relay.state.ws.readyState === 1", timeout=15000)

    b.goto(f"{BASE}/r/{room}", wait_until="domcontentloaded")
    b.wait_for_selector("#pwView:not(.hidden)", timeout=20000)

    COUNT_EXPR = "window.__relay.logs().filter(l => l.includes('close 4005')).length"

    def close_count():
        return b.evaluate(f"() => {COUNT_EXPR}")

    # The initial page-load attempt (no password) is one refused close.
    b.wait_for_function(f"() => {COUNT_EXPR} >= 1", timeout=20000)
    print("PASS password prompt shown", flush=True)

    for i in range(8):
        before = close_count()
        b.fill("#pwInput", "wrong-" + secrets.token_hex(4))
        b.click("#pwBtn")
        b.wait_for_function(f"(n) => ({COUNT_EXPR}) > n", arg=before, timeout=20000)
    print("PASS 8 wrong attempts rejected", flush=True)

    b.fill("#pwInput", PASSWORD)
    b.click("#pwBtn")
    time.sleep(2.0)
    still_prompt = b.evaluate("() => !document.getElementById('pwView').classList.contains('hidden')")
    if not still_prompt:
        raise AssertionError("correct password accepted while blocked")
    logs = b.evaluate("() => window.__relay.logs()")
    if not any("too_many_attempts" in line for line in logs):
        raise AssertionError(f"block reason missing; last logs: {logs[-5:]}")
    print("PASS correct password blocked with too_many_attempts", flush=True)

    # The creator's existing connection is not disturbed.
    alive = a.evaluate("() => window.__relay.state.ws && window.__relay.state.ws.readyState === 1")
    if not alive:
        raise AssertionError("creator connection was dropped by the brake")
    print("PASS creator connection unaffected", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)

