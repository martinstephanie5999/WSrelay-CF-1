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
BROWSER_CHANNEL = os.environ.get("RELAY_BROWSER", "msedge")
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / "loss-2m.bin"
if not FILE.exists() or FILE.stat().st_size != 2 * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        f.write(block)
        f.write(block)

source_hash = hashlib.sha256(FILE.read_bytes()).hexdigest()
room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}?auto=1"
print(f"INFO room={room} url={url} hash={source_hash}", flush=True)

STATE_JS = """() => ({
  ready: window.__relay ? window.__relay.state.peerReady : null,
  transfers: window.__relay ? window.__relay.getTransfers() : null,
  logs: window.__relay ? window.__relay.logs().slice(-20) : null,
})"""

with sync_playwright() as p:
    tmp_a = tempfile.mkdtemp(prefix="relay-loss-a-")
    tmp_b = tempfile.mkdtemp(prefix="relay-loss-b-")
    ctx_a = p.chromium.launch_persistent_context(
        tmp_a, channel=BROWSER_CHANNEL, headless=True
    )
    ctx_b = p.chromium.launch_persistent_context(
        tmp_b, channel=BROWSER_CHANNEL, headless=True
    )
    a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()
    errors = []
    for name, page in (("A", a), ("B", b)):
        page.on("pageerror", lambda err, n=name: errors.append(f"{n} pageerror: {err}"))
        page.on("console", lambda msg, n=name: errors.append(f"{n} console.{msg.type}: {msg.text}") if msg.type == "error" else None)

    a.goto(url, wait_until="domcontentloaded")
    b.goto(url, wait_until="domcontentloaded")
    a.wait_for_function("() => Boolean(window.__relay)", timeout=20000)
    b.wait_for_function("() => Boolean(window.__relay)", timeout=20000)
    a.wait_for_function("() => window.__relay.state.peerReady === true", timeout=45000)
    b.wait_for_function("() => window.__relay.state.peerReady === true", timeout=45000)
    print("PASS peer ready", flush=True)

    a.evaluate("() => window.__relay.dropNextBinary(1)")
    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")
    print("INFO sent with one dropped frame", flush=True)

    started = time.time()
    b.wait_for_function(
        """() => window.__relay.getTransfers().some(
             t => t.direction === 'recv' && t.state === 'received')""",
        timeout=120000,
    )
    a.wait_for_function(
        """() => window.__relay.getTransfers().some(
             t => t.direction === 'send' && t.state === 'completed')""",
        timeout=120000,
    )
    elapsed = time.time() - started
    print(f"PASS transfer completed after drop ({elapsed:.1f}s)", flush=True)

    recv = b.evaluate("() => window.__relay.getTransfers().filter(t => t.direction === 'recv')")
    file_id = recv[0]["fileId"]
    got_hash = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", file_id)
    print(f"INFO hash={got_hash}", flush=True)
    if got_hash != source_hash:
        raise AssertionError(f"hash mismatch: {got_hash} != {source_hash}")
    print("PASS SHA-256 matches after retransmission", flush=True)

    logs = a.evaluate("() => window.__relay.logs()")
    print("INFO sender logs:", flush=True)
    for line in logs[-12:]:
        print(" ", line, flush=True)
    if not any("丢弃一帧" in line for line in logs):
        raise AssertionError("drop hook did not fire")
    if not any(("offset_gap" in line) or ("回退" in line) for line in logs):
        raise AssertionError("retransmission path did not trigger")

    print(f"INFO errors={errors}", flush=True)
    ctx_a.close()
    ctx_b.close()
    shutil.rmtree(tmp_a, ignore_errors=True)
    shutil.rmtree(tmp_b, ignore_errors=True)

