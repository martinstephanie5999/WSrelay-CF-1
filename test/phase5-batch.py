"""Phase 5: multi-file batch transfer, each file independently verified.

Usage: python phase5-batch.py [BASE]
"""

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
LAUNCH_ARGS = ["--no-proxy-server"] if os.environ.get("RELAY_DIRECT") == "1" else []
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
BATCH_DIR = DATA_DIR / "batch-src"
BATCH_DIR.mkdir(exist_ok=True)

specs = [("01-alpha.bin", 1), ("02-beta.bin", 2), ("03-gamma.bin", 3), ("04-delta.bin", 1), ("05-epsilon.bin", 2)]
files = []
for name, mib in specs:
    p = BATCH_DIR / name
    if not p.exists() or p.stat().st_size != mib * 1024 * 1024:
        block = os.urandom(1024 * 1024)
        with open(p, "wb") as f:
            for _ in range(mib):
                f.write(block)
    files.append(p)

hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
print(f"INFO batch: {[p.name for p in files]}", flush=True)

tmp_a = tempfile.mkdtemp(prefix="relay-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-b-")
with sync_playwright() as p:
    ctx_a = p.chromium.launch_persistent_context(
        tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, args=LAUNCH_ARGS
    )
    ctx_b = p.chromium.launch_persistent_context(
        tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, args=LAUNCH_ARGS
    )
    a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()
    a.goto(BASE + "/", wait_until="domcontentloaded")
    a.click("#createBtn")
    a.wait_for_function("() => Boolean(window.__relay && window.__relay.state.roomId)", timeout=15000)
    room = a.evaluate("() => window.__relay.state.roomId")
    b.goto(f"{BASE}/r/{room}?auto=1", wait_until="domcontentloaded")
    a.wait_for_function("() => window.__relay.state.peerReady === true", timeout=30000)
    b.wait_for_function("() => window.__relay.state.peerReady === true", timeout=30000)

    a.set_input_files("#fileInput", [str(x) for x in files])
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")

    b.wait_for_function(
        "() => window.__relay.getTransfers().filter(t => t.direction === 'recv' && t.state === 'received').length === 5",
        timeout=180000,
    )
    a.wait_for_function(
        "() => window.__relay.getTransfers().filter(t => t.direction === 'send' && t.state === 'completed').length === 5",
        timeout=180000,
    )
    print("PASS all 5 files transferred", flush=True)

    recv = b.evaluate("() => window.__relay.getTransfers().filter(t => t.direction === 'recv')")
    names = sorted(t["name"] for t in recv)
    if names != sorted(hashes):
        raise AssertionError(f"names mismatch: {names}")
    for t in recv:
        got = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", t["fileId"])
        if got != hashes[t["name"]]:
            raise AssertionError(f"hash mismatch for {t['name']}")
    print("PASS per-file SHA-256 matches", flush=True)

    hint = a.inner_text("#batchHint")
    if "5/5" not in hint:
        raise AssertionError(f"batch hint wrong: {hint}")
    print(f"PASS batch hint: {hint}", flush=True)

    logs = a.evaluate("() => window.__relay.logs()")
    if not any("01-alpha.bin" in line for line in logs):
        raise AssertionError("path missing from logs")

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


