"""ACK loss test: the receiver drops the next 3 ack frames; the sender must
recover via ack-query/retransmit and still finish with a matching hash.

Usage: python phase2-ack-loss.py [BASE]
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
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / "ackloss-8m.bin"
if not FILE.exists() or FILE.stat().st_size != 8 * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(8):
            f.write(block)

source_hash = hashlib.sha256(FILE.read_bytes()).hexdigest()
room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}?auto=1"
print(f"INFO room={room} hash={source_hash}", flush=True)

with sync_playwright() as p:
    tmp_a = tempfile.mkdtemp(prefix="relay-a-")
    tmp_b = tempfile.mkdtemp(prefix="relay-b-")
    ctx_a = p.chromium.launch_persistent_context(tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    ctx_b = p.chromium.launch_persistent_context(tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()
    a.goto(url, wait_until="domcontentloaded")
    b.goto(url, wait_until="domcontentloaded")
    a.wait_for_function("() => window.__relay && window.__relay.state.peerReady === true", timeout=45000)
    b.wait_for_function("() => window.__relay && window.__relay.state.peerReady === true", timeout=45000)
    print("PASS peer ready", flush=True)

    b.evaluate("() => window.__relay.dropNextControl('ack', 3)")
    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    started = time.time()
    a.click("#sendBtn")

    a.wait_for_function(
        "() => { const t = window.__relay.getTransfers().find(x => x.direction === 'send'); return t && (t.state === 'completed' || t.state === 'failed'); }",
        timeout=120000,
    )
    elapsed = time.time() - started
    st = a.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'send')")
    if st["state"] != "completed":
        raise AssertionError(f"send failed: {st}")
    print(f"PASS completed despite 3 dropped ACKs in {elapsed:.1f}s", flush=True)

    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=30000,
    )
    recv = b.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'recv')")
    got_hash = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", recv["fileId"])
    if got_hash != source_hash:
        raise AssertionError(f"hash mismatch: {got_hash} != {source_hash}")
    print("PASS SHA-256 matches", flush=True)

    logs_a = a.evaluate("() => window.__relay.logs()")
    logs_b = b.evaluate("() => window.__relay.logs()")
    drops = sum(1 for line in logs_b if "丢弃控制帧 ack" in line)
    if drops < 1:
        raise AssertionError("ack drop hook did not fire")
    if not any("ACK 停滞" in line for line in logs_a):
        raise AssertionError("ACK stall watchdog did not fire")
    print(f"PASS watchdog fired (dropped {drops} acks seen in receiver logs)", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


