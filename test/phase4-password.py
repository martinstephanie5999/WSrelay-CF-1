"""Phase 4: password room flow.

A creates a room with a password. B opens the link without a password and
must be asked for one; a wrong password keeps the prompt; the right password
joins and a small transfer completes with a matching hash.

Usage: python phase4-password.py [BASE]
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
FILE = DATA_DIR / "pw-2m.bin"
if not FILE.exists() or FILE.stat().st_size != 2 * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        f.write(block)
        f.write(block)
source_hash = hashlib.sha256(FILE.read_bytes()).hexdigest()

PASSWORD = "correct horse battery" + secrets.token_hex(3)
print(f"INFO password set (len={len(PASSWORD)})", flush=True)

tmp_a = tempfile.mkdtemp(prefix="relay-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-b-")
with sync_playwright() as p:
    ctx_a = p.chromium.launch_persistent_context(tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    ctx_b = p.chromium.launch_persistent_context(tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()
    errors = []
    for name, page in (("A", a), ("B", b)):
        page.on("pageerror", lambda err, n=name: errors.append(f"{n} {err}"))

    a.goto(BASE + "/", wait_until="domcontentloaded")
    a.fill("#passwordInput", PASSWORD)
    a.click("#createBtn")
    a.wait_for_function("() => Boolean(window.__relay && window.__relay.state.roomId)", timeout=15000)
    room = a.evaluate("() => window.__relay.state.roomId")
    link = f"{BASE}/r/{room}?auto=1"
    print(f"INFO room={room}", flush=True)
    if PASSWORD in link:
        raise AssertionError("password leaked into URL")
    a.wait_for_function("() => window.__relay.state.peerReady === false", timeout=5000)

    b.goto(link, wait_until="domcontentloaded")
    b.wait_for_selector("#pwView:not(.hidden)", timeout=20000)
    print("PASS B was asked for a password", flush=True)
    if "密码" not in b.inner_text("#pwHint"):
        raise AssertionError("missing password hint")

    b.fill("#pwInput", "wrong " + PASSWORD)
    b.click("#pwBtn")
    b.wait_for_selector("#pwView:not(.hidden)", timeout=15000)
    time.sleep(1.0)
    print("PASS wrong password kept the prompt", flush=True)

    b.fill("#pwInput", PASSWORD)
    b.click("#pwBtn")
    a.wait_for_function("() => window.__relay.state.peerReady === true", timeout=30000)
    b.wait_for_function("() => window.__relay.state.peerReady === true", timeout=30000)
    print("PASS correct password joined", flush=True)

    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")
    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=60000,
    )
    recv = b.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'recv')")
    got = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", recv["fileId"])
    if got != source_hash:
        raise AssertionError(f"hash mismatch: {got} != {source_hash}")
    print("PASS transfer in password room, SHA-256 matches", flush=True)

    if errors:
        raise AssertionError(f"page errors: {errors}")
    print("PASS no page errors", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


