"""Pause/resume test: sender pauses mid-transfer, offsets must freeze,
then resume completes with a matching hash.

Usage: python phase2-pause.py [BASE] [SIZE_MIB]
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
SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 64

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / f"pause-{SIZE}m.bin"
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

tmp_a = tempfile.mkdtemp(prefix="relay-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-b-")
with sync_playwright() as p:
    ctx_a = p.chromium.launch_persistent_context(tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    ctx_b = p.chromium.launch_persistent_context(tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()
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
    tid = a.evaluate(SEND_JS)["transferId"]
    a.evaluate("(tid) => window.__relay.pause(tid)", tid)
    time.sleep(0.5)
    s1 = a.evaluate(SEND_JS)
    r1 = b.evaluate(RECV_JS)
    print(f"INFO paused at sent={s1['sentOffset']} recv={r1['receivedBytes']}", flush=True)
    if not s1["paused"]:
        raise AssertionError("sender not marked paused")
    if s1["state"] == "completed":
        raise AssertionError("transfer finished before pause took effect")

    time.sleep(3.0)
    s2 = a.evaluate(SEND_JS)
    if s2["sentOffset"] != s1["sentOffset"]:
        raise AssertionError(
            f"sentOffset advanced while paused: {s1['sentOffset']} -> {s2['sentOffset']}"
        )
    print("PASS sentOffset frozen during pause", flush=True)

    a.evaluate("(tid) => window.__relay.resume(tid)", tid)
    a.wait_for_function(
        "() => { const t = window.__relay.getTransfers().find(x => x.direction === 'send'); return t && (t.state === 'completed' || t.state === 'failed'); }",
        timeout=120000,
    )
    st = a.evaluate(SEND_JS)
    if st["state"] != "completed":
        raise AssertionError(f"send failed: {st}")
    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=30000,
    )
    recv = b.evaluate(RECV_JS)
    got_hash = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", recv["fileId"])
    if got_hash != source_hash:
        raise AssertionError(f"hash mismatch: {got_hash} != {source_hash}")
    print("PASS transfer completed after resume, SHA-256 matches", flush=True)

    logs = a.evaluate("() => window.__relay.logs()")
    if not any("已暂停" in line for line in logs):
        raise AssertionError("pause log missing")
    if not any("继续发送" in line for line in logs):
        raise AssertionError("resume log missing")

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


