"""Phase 3: reload the receiver page mid-transfer. The new page must resume
from OPFS .meta (receivedBytes) and finish with a matching hash.

Usage: python phase3-reload.py [BASE] [SIZE_MIB]
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
FILE = DATA_DIR / f"reload-{SIZE}m.bin"
if not FILE.exists() or FILE.stat().st_size != SIZE * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(SIZE):
            f.write(block)

source_hash = hashlib.sha256(FILE.read_bytes()).hexdigest()
room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}?auto=1"
print(f"INFO room={room} size={SIZE}MiB hash={source_hash}", flush=True)

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

    threshold = SIZE // 3 * 1024 * 1024
    b.wait_for_function(
        "(n) => { const t = window.__relay.getTransfers().find(x => x.direction === 'recv'); return t && t.receivedBytes > n; }",
        arg=threshold,
        timeout=60000,
    )
    r0 = b.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'recv')")
    print(f"INFO reloading receiver at recv={r0['receivedBytes']}", flush=True)

    b.reload(wait_until="domcontentloaded")
    b.wait_for_function("() => Boolean(window.__relay)", timeout=30000)
    b.wait_for_function("() => window.__relay.state.peerReady === true", timeout=45000)
    print("PASS receiver rejoined after reload", flush=True)

    a.wait_for_function(
        "() => { const t = window.__relay.getTransfers().find(x => x.direction === 'send'); return t && (t.state === 'completed' || t.state === 'failed'); }",
        timeout=180000,
    )
    st = a.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'send')")
    if st["state"] != "completed":
        raise AssertionError(f"send failed after reload: {st}")
    print("PASS sender completed after receiver reload", flush=True)

    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=60000,
    )
    recv = b.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'recv')")
    got = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", recv["fileId"])
    if got != source_hash:
        raise AssertionError(f"hash mismatch: {got} != {source_hash}")
    if recv["receivedBytes"] != SIZE * 1024 * 1024:
        raise AssertionError(f"size mismatch: {recv['receivedBytes']}")
    print("PASS SHA-256 matches after reload", flush=True)

    logs = b.evaluate("() => window.__relay.logs()")
    if not any("恢复接收状态" in line for line in logs):
        raise AssertionError("receiver did not resume from meta after reload")

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


