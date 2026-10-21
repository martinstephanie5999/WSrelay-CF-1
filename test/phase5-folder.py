"""Phase 5: folder transfer (webkitdirectory), paths preserved as metadata.

Usage: python phase5-folder.py [BASE]
"""

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
ROOT = DATA_DIR / "folder-src"
(ROOT / "sub").mkdir(parents=True, exist_ok=True)

specs = [("a.bin", 1), ("sub/b.bin", 2), ("sub/c.bin", 1), ("z.txt", 1)]
expected = {}
for rel, mib in specs:
    p = ROOT / rel
    if not p.exists() or p.stat().st_size != mib * 1024 * 1024:
        block = os.urandom(1024 * 1024)
        with open(p, "wb") as f:
            for _ in range(mib):
                f.write(block)
    expected[rel] = hashlib.sha256(p.read_bytes()).hexdigest()

tmp_a = tempfile.mkdtemp(prefix="relay-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-b-")
with sync_playwright() as p:
    ctx_a = p.chromium.launch_persistent_context(tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    ctx_b = p.chromium.launch_persistent_context(tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()
    a.goto(BASE + "/", wait_until="domcontentloaded")
    a.click("#createBtn")
    a.wait_for_function("() => Boolean(window.__relay && window.__relay.state.roomId)", timeout=15000)
    room = a.evaluate("() => window.__relay.state.roomId")
    b.goto(f"{BASE}/r/{room}?auto=1", wait_until="domcontentloaded")
    a.wait_for_function("() => window.__relay.state.peerReady === true", timeout=30000)
    b.wait_for_function("() => window.__relay.state.peerReady === true", timeout=30000)

    a.set_input_files("#folderInput", str(ROOT))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")
    b.wait_for_function(
        "() => window.__relay.getTransfers().filter(t => t.direction === 'recv' && t.state === 'received').length === 4",
        timeout=180000,
    )
    print("PASS folder files transferred", flush=True)

    recv = b.evaluate("() => window.__relay.getTransfers().filter(t => t.direction === 'recv')")
    rel_paths = []
    for t in recv:
        # path carries the sender's webkitRelativePath, e.g. folder-src/sub/b.bin
        marker = next((r for r in expected if t["path"] and t["path"].endswith(r)), None)
        if marker is None:
            raise AssertionError(f"cannot map received path {t['path']!r}")
        rel_paths.append(marker)
        got = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", t["fileId"])
        if got != expected[marker]:
            raise AssertionError(f"hash mismatch for {marker}")
    if sorted(rel_paths) != sorted(expected):
        raise AssertionError(f"paths mismatch: {rel_paths}")
    print(f"PASS folder paths + hashes: {sorted(rel_paths)}", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


