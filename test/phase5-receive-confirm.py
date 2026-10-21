"""Receive-confirmation regression.

A sends a file while B keeps the default (confirm) mode. B must see an
offered row and no transfer must start until B clicks accept. Rejecting must
fail the sender's transfer; sending again and accepting must complete.

Usage: python phase5-receive-confirm.py [BASE]
"""

import hashlib
import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
SIZE = 2
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / "receive-confirm-2m.bin"
if not FILE.exists() or FILE.stat().st_size != SIZE * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(SIZE):
            f.write(block)

source_hash = hashlib.sha256(FILE.read_bytes()).hexdigest()
room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}"
print(f"INFO room={room} size={SIZE}MiB hash={source_hash}", flush=True)

SEND_JS = """() => window.__relay.getTransfers().find(t => t.direction === 'send')"""
RECV_JS = """() => window.__relay.getTransfers().find(t => t.direction === 'recv')"""

tmp_a = tempfile.mkdtemp(prefix="relay-rc-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-rc-b-")
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

    if b.evaluate("() => window.__relay.state.autoAccept") is not False:
        raise AssertionError("B default autoAccept should be false")

    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")

    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'offered')",
        timeout=30000,
    )
    offered = b.evaluate(RECV_JS)
    print(f"INFO offered: {offered['state']} {offered['expectedSize']}", flush=True)
    if offered["state"] != "offered":
        raise AssertionError(f"expected offered, got {offered}")
    if not b.locator("#incoming .item .accept").first.is_visible():
        raise AssertionError("accept button not visible")
    if not b.locator("#incoming .item .reject").first.is_visible():
        raise AssertionError("reject button not visible")
    sent = a.evaluate(SEND_JS)
    if sent["state"] != "offering":
        raise AssertionError(f"sender should still be offering: {sent}")
    print("PASS offer waits for confirmation", flush=True)

    b.locator("#incoming .item .reject").first.click()
    b.wait_for_function(
        "() => !window.__relay.getTransfers().some(t => t.direction === 'recv')",
        timeout=15000,
    )
    a.wait_for_function(
        "() => { const t = window.__relay.getTransfers().find(x => x.direction === 'send'); return t && t.state === 'failed'; }",
        timeout=15000,
    )
    print("PASS reject fails the sender", flush=True)

    # Send again; this time accept.
    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")
    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'offered')",
        timeout=30000,
    )
    b.locator("#incoming .item .accept").first.click()
    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=60000,
    )
    try:
        a.wait_for_function(
            "() => window.__relay.getTransfers().some(t => t.direction === 'send' && t.state === 'completed')",
            timeout=30000,
        )
    except Exception:
        print("DEBUG A:", a.evaluate(SEND_JS), flush=True)
        print("DEBUG B:", b.evaluate(RECV_JS), flush=True)
        print("DEBUG A logs:", a.evaluate("() => window.__relay.logs().slice(-20)"), flush=True)
        print("DEBUG B logs:", b.evaluate("() => window.__relay.logs().slice(-20)"), flush=True)
        raise
    recv = b.evaluate(RECV_JS)
    got_hash = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", recv["fileId"])
    if got_hash != source_hash:
        raise AssertionError(f"hash mismatch: {got_hash} != {source_hash}")
    print("PASS accept completes with matching hash", flush=True)

    if errors:
        raise AssertionError(f"page errors: {errors}")
    print("PASS no page errors", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)

