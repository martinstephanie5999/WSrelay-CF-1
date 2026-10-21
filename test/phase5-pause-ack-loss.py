"""Pause + lost-ACK regression.

A slow receiver fills the sender window; the next ACKs are dropped, then the
sender pauses and resumes. Without waking ackWaiters the pump would stay
blocked in waitForAck forever after resume.

Usage: python phase5-pause-ack-loss.py [BASE]
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
SIZE = 16
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / f"pause-ack-{SIZE}m.bin"
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

tmp_a = tempfile.mkdtemp(prefix="relay-pa-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-pa-b-")
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

    # Slow receiver so the sender fills its 2 MiB window before ACKs.
    b.evaluate("() => window.__relay.setWriteDelay(200)")
    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")

    a.wait_for_function(
        """() => {
             const t = window.__relay.getTransfers().find(x => x.direction === 'send');
             return t && t.sentOffset - t.ackedOffset >= 8 * 256 * 1024;
           }""",
        timeout=30000,
    )
    tid = a.evaluate(SEND_JS)["transferId"]
    # Drop the ACKs that would otherwise wake the paused pump.
    b.evaluate("() => window.__relay.dropNextControl('ack', 3)")
    a.evaluate("(tid) => window.__relay.pause(tid)", tid)
    time.sleep(0.8)
    s1 = a.evaluate(SEND_JS)
    print(f"INFO paused at sent={s1['sentOffset']} acked={s1['ackedOffset']}", flush=True)
    if not s1["paused"]:
        raise AssertionError("sender not marked paused")

    time.sleep(2.5)
    s2 = a.evaluate(SEND_JS)
    if s2["sentOffset"] != s1["sentOffset"]:
        raise AssertionError(
            f"sentOffset advanced while paused: {s1['sentOffset']} -> {s2['sentOffset']}"
        )
    print("PASS sentOffset frozen during pause", flush=True)

    a.evaluate("(tid) => window.__relay.resume(tid)", tid)
    try:
        a.wait_for_function(
            "() => { const t = window.__relay.getTransfers().find(x => x.direction === 'send'); return t && (t.state === 'completed' || t.state === 'failed'); }",
            timeout=120000,
        )
    except Exception:
        print("DEBUG A transfer:", a.evaluate(SEND_JS), flush=True)
        print("DEBUG B transfer:", b.evaluate(RECV_JS), flush=True)
        print("DEBUG A logs:", a.evaluate("() => window.__relay.logs().slice(-25)"), flush=True)
        print("DEBUG B logs:", b.evaluate("() => window.__relay.logs().slice(-25)"), flush=True)
        raise
    st = a.evaluate(SEND_JS)
    if st["state"] != "completed":
        raise AssertionError(f"send failed after pause+ACK loss: {st}")

    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=60000,
    )
    recv = b.evaluate(RECV_JS)
    got_hash = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", recv["fileId"])
    if got_hash != source_hash:
        raise AssertionError(f"hash mismatch: {got_hash} != {source_hash}")
    print("PASS completed after pause + lost ACKs, SHA-256 matches", flush=True)

    if errors:
        raise AssertionError(f"page errors: {errors}")
    print("PASS no page errors", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


