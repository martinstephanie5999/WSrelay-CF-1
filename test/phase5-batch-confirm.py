"""Batch confirmation regression: with default confirm mode, clicking
"本批全部接收" on the first offer accepts the rest of the same batch.

Usage: python phase5-batch-confirm.py [BASE]
"""

import hashlib
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
COUNT = 3
SIZE = 1024 * 1024
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILES = []
HASHES = {}
for i in range(COUNT):
    p = DATA_DIR / f"batch-confirm-{i+1}.bin"
    if not p.exists() or p.stat().st_size != SIZE:
        p.write_bytes(os.urandom(SIZE))
    FILES.append(p)
    HASHES[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()

room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}"
print(f"INFO room={room} files={[p.name for p in FILES]}", flush=True)

tmp_a = tempfile.mkdtemp(prefix="relay-bc-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-bc-b-")
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

    a.set_input_files("#fileInput", [str(p) for p in FILES])
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")

    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'offered')",
        timeout=30000,
    )
    if not b.locator("#incoming .item .accept-batch").first.is_visible():
        raise AssertionError("batch accept button not visible")
    b.locator("#incoming .item .accept-batch").first.click()
    print("PASS clicked batch accept", flush=True)

    b.wait_for_function(
        "(n) => window.__relay.getTransfers().filter(t => t.direction === 'recv' && t.state === 'received').length === n",
        arg=COUNT,
        timeout=120000,
    )
    received = b.evaluate("() => window.__relay.getTransfers().filter(t => t.direction === 'recv')")
    if len(received) != COUNT:
        raise AssertionError(f"expected {COUNT} recv rows, got {len(received)}")
    for t in received:
        got = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", t["fileId"])
        want = HASHES.get(t["name"])
        if not want or got != want:
            raise AssertionError(f"hash mismatch for {t['name']}: {got} != {want}")
    print("PASS all batch files received with matching hashes", flush=True)

    a.wait_for_function(
        "(n) => window.__relay.getTransfers().filter(t => t.direction === 'send' && t.state === 'completed').length === n",
        arg=COUNT,
        timeout=60000,
    )
    print("PASS sender completed all batch files", flush=True)

    if errors:
        raise AssertionError(f"page errors: {errors}")
    print("PASS no page errors", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)

