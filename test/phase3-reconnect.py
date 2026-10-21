"""Phase 3: force-drop both sockets mid-transfer; reconnect must resume from
the receiver's persisted offset and finish with a matching hash.

Usage: python phase3-reconnect.py [BASE] [SIZE_MIB]
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
FILE = DATA_DIR / f"reconnect-{SIZE}m.bin"
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

    half = SIZE // 3 * 1024 * 1024
    b.wait_for_function(
        "(n) => { const t = window.__relay.getTransfers().find(x => x.direction === 'recv'); return t && t.receivedBytes > n; }",
        arg=half,
        timeout=60000,
    )
    r0 = b.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'recv')")
    print(f"INFO dropping sockets at recv={r0['receivedBytes']}", flush=True)
    a.evaluate("() => window.__relay.state.ws.close()")
    b.evaluate("() => window.__relay.state.ws.close()")

    try:
        a.wait_for_function(
            "() => { const t = window.__relay.getTransfers().find(x => x.direction === 'send'); return t && (t.state === 'completed' || t.state === 'failed'); }",
            timeout=120000,
        )
    except Exception:
        for name, page in (("A", a), ("B", b)):
            try:
                print(f"DUMP {name} transfers: {page.evaluate('() => window.__relay.getTransfers()')}", flush=True)
                print(f"DUMP {name} logs:", flush=True)
                for line in page.evaluate("() => window.__relay.logs().slice(-25)"):
                    print("  ", line, flush=True)
            except Exception as dump_err:
                print(f"WARN {name} dump failed: {dump_err}", flush=True)
        raise
    st = a.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'send')")
    if st["state"] != "completed":
        raise AssertionError(f"send failed after reconnect: {st}")
    print("PASS sender completed after reconnect", flush=True)

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
    print("PASS SHA-256 matches after reconnect", flush=True)

    logs = a.evaluate("() => window.__relay.logs()")
    if not any("连接关闭" in line for line in logs):
        raise AssertionError("reconnect was not exercised")

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


