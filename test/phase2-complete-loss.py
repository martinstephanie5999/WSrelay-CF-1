"""Drops the sender's `complete` control frame and verifies quick recovery.

Old behavior: a lost `complete` cost a fixed 30s waiting on complete-ack.
New behavior: retry after 4s (backoff), so the transfer completes well
inside 20s and the sender log shows the resend.

Usage: python phase2-complete-loss.py [BASE]
"""

import hashlib
import os
import secrets
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / "complete-loss-4m.bin"
if not FILE.exists() or FILE.stat().st_size != 4 * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(4):
            f.write(block)

source_hash = hashlib.sha256(FILE.read_bytes()).hexdigest()
room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}?auto=1"
print(f"INFO room={room} hash={source_hash}", flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    a = browser.new_context().new_page()
    b = browser.new_context().new_page()
    errors = []
    for name, page in (("A", a), ("B", b)):
        page.on("pageerror", lambda err, n=name: errors.append(f"{n} pageerror: {err}"))

    a.goto(url, wait_until="domcontentloaded")
    b.goto(url, wait_until="domcontentloaded")
    a.wait_for_function("() => window.__relay && window.__relay.state.peerReady === true", timeout=45000)
    b.wait_for_function("() => window.__relay && window.__relay.state.peerReady === true", timeout=45000)
    print("PASS peer ready", flush=True)

    a.evaluate("() => window.__relay.dropNextControl('complete')")
    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)

    started = time.time()
    a.click("#sendBtn")
    print("INFO sent with complete frame dropped", flush=True)

    a.wait_for_function(
        """() => window.__relay.getTransfers().some(
             t => t.direction === 'send' && (t.state === 'completed' || t.state === 'failed'))""",
        timeout=25000,
    )
    elapsed = time.time() - started
    send_t = a.evaluate(
        "() => window.__relay.getTransfers().find(t => t.direction === 'send')"
    )
    if send_t["state"] != "completed":
        raise AssertionError(f"send failed: {send_t}")
    print(f"PASS sender completed in {elapsed:.1f}s (was 30s+ with fixed timeout)", flush=True)
    if elapsed > 20:
        raise AssertionError(f"recovery too slow: {elapsed:.1f}s")

    b.wait_for_function(
        """() => window.__relay.getTransfers().some(
             t => t.direction === 'recv' && t.state === 'received')""",
        timeout=25000,
    )
    recv = b.evaluate("() => window.__relay.getTransfers().filter(t => t.direction === 'recv')")
    got_hash = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", recv[0]["fileId"])
    if got_hash != source_hash:
        raise AssertionError(f"hash mismatch: {got_hash} != {source_hash}")
    print("PASS SHA-256 matches", flush=True)

    logs = a.evaluate("() => window.__relay.logs()")
    if not any("丢弃控制帧 complete" in line for line in logs):
        raise AssertionError("drop hook did not fire")
    if not any("complete 无应答，重发" in line for line in logs):
        raise AssertionError("complete retry did not fire")
    print("PASS retry observed in sender logs", flush=True)
    if errors:
        raise AssertionError(f"browser errors: {errors}")
    browser.close()


