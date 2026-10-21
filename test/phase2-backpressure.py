"""Backpressure test: a slow receiver must stall the sender at the window
limit (in-flight bytes <= windowSize * chunkSize) without breaking the
transfer.

Usage: python phase2-backpressure.py [BASE]
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
# Must match CHUNK_SIZE in src/protocol.js (256 KiB).
CHUNK = 256 * 1024
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / f"backpressure-{SIZE}m.bin"
if not FILE.exists() or FILE.stat().st_size != SIZE * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(SIZE):
            f.write(block)

source_hash = hashlib.sha256(FILE.read_bytes()).hexdigest()
room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}?auto=1"
print(f"INFO room={room} hash={source_hash}", flush=True)

SEND_JS = "() => window.__relay.getTransfers().find(t => t.direction === 'send')"

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

    # 200ms per chunk -> ~1.25 MiB/s persist rate, far below loopback speed.
    b.evaluate("() => window.__relay.setWriteDelay(200)")
    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    started = time.time()
    a.click("#sendBtn")

    max_inflight = 0
    max_allowed = 0
    completed = False
    while time.time() - started < 180:
        st = a.evaluate(SEND_JS)
        if st:
            inflight = st["sentOffset"] - st["ackedOffset"]
            allowed = st["windowSize"] * CHUNK
            max_inflight = max(max_inflight, inflight)
            max_allowed = max(max_allowed, allowed)
            if st["state"] in ("completed", "failed"):
                completed = st["state"] == "completed"
                break
        time.sleep(0.25)

    if not completed:
        raise AssertionError("transfer did not complete under backpressure")
    print(
        f"PASS completed in {time.time()-started:.1f}s; "
        f"max in-flight {max_inflight/1024/1024:.2f} MiB "
        f"(max window allowed {max_allowed/1024/1024:.2f} MiB)",
        flush=True,
    )
    if max_inflight > max_allowed + CHUNK:
        raise AssertionError(
            f"in-flight {max_inflight} exceeded window {max_allowed} (+1 chunk)"
        )

    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=30000,
    )
    recv = b.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'recv')")
    got_hash = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", recv["fileId"])
    if got_hash != source_hash:
        raise AssertionError(f"hash mismatch: {got_hash} != {source_hash}")
    print("PASS SHA-256 matches", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


