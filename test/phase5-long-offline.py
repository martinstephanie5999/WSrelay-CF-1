"""Long-offline regression: a send must not become permanently failed after
the peer is gone for more than 3 seconds. It suspends to offering and resumes
from the receiver's persisted offset on reconnect.

Usage: python phase5-long-offline.py [BASE]
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
SIZE = 64
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / f"long-offline-{SIZE}m.bin"
if not FILE.exists() or FILE.stat().st_size != SIZE * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(SIZE):
            f.write(block)

source_hash = hashlib.sha256(FILE.read_bytes()).hexdigest()
room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}?auto=1"
print(f"INFO room={room} size={SIZE}MiB hash={source_hash}", flush=True)

SEND_JS = """() => window.__relay.getTransfers().find(t => t.direction === 'send')"""
RECV_JS = """() => window.__relay.getTransfers().find(t => t.direction === 'recv')"""

tmp_a = tempfile.mkdtemp(prefix="relay-lo-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-lo-b-")
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
    a.wait_for_function("() => window.__relay && window.__relay.state.peerReady === true", timeout=45000)
    b.wait_for_function("() => window.__relay && window.__relay.state.peerReady === true", timeout=45000)
    print("PASS peer ready", flush=True)

    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")

    a.wait_for_function(
        "() => { const t = window.__relay.getTransfers().find(x => x.direction === 'send'); return t && t.sentOffset > 8 * 1024 * 1024; }",
        timeout=30000,
    )
    before = a.evaluate(SEND_JS)
    print(f"INFO going offline at sent={before['sentOffset']} acked={before['ackedOffset']}", flush=True)

    # Stop B's auto-reconnect and close its socket for >3s.
    b.evaluate("() => { window.__relay.setReconnectPaused(true); window.__relay.state.ws.close(); }")
    time.sleep(5.0)

    offline = a.evaluate(SEND_JS)
    print(f"INFO after 5s offline: state={offline['state']} acked={offline['ackedOffset']}", flush=True)
    if offline["state"] == "failed":
        raise AssertionError(f"send failed after long offline: {offline}")
    if offline["state"] != "offering":
        raise AssertionError(f"send not suspended to offering: {offline}")
    if not offline["ackedOffset"]:
        raise AssertionError(f"ackedOffset was lost: {offline}")
    print("PASS send suspended instead of failed", flush=True)

    # Let B reconnect; A should re-offer from the persisted offset.
    b.evaluate("() => window.__relay.setReconnectPaused(false)")
    a.wait_for_function(
        "() => { const t = window.__relay.getTransfers().find(x => x.direction === 'send'); return t && (t.state === 'completed' || t.state === 'failed'); }",
        timeout=180000,
    )
    st = a.evaluate(SEND_JS)
    if st["state"] != "completed":
        raise AssertionError(f"send failed after reconnect: {st}")

    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=60000,
    )
    recv = b.evaluate(RECV_JS)
    got_hash = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", recv["fileId"])
    if got_hash != source_hash:
        raise AssertionError(f"hash mismatch: {got_hash} != {source_hash}")
    print("PASS completed after 5s offline, SHA-256 matches", flush=True)

    if errors:
        raise AssertionError(f"page errors: {errors}")
    print("PASS no page errors", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


