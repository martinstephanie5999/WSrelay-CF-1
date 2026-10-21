"""Quick inspector: run one small transfer and dump both pages' logs.

Used to see receiver-side errors when a write stalls without surfacing in
transfer state. Persistent contexts (disk-backed OPFS).

Usage: python probe-inspect.py [BASE] [SIZE_MIB]
"""

import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 4
LAUNCH_ARGS = ["--no-proxy-server"] if os.environ.get("RELAY_DIRECT") == "1" else []

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / f"inspect-{SIZE}m.bin"
if not FILE.exists() or FILE.stat().st_size != SIZE * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(SIZE):
            f.write(block)

room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}?auto=1"
print(f"INFO room={room} size={SIZE}MiB", flush=True)

STATE_JS = """() => ({
  transfers: window.__relay ? window.__relay.getTransfers() : null,
  logs: window.__relay ? window.__relay.logs().slice(-12) : [],
})"""

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

    for name, page in (("A", a), ("B", b)):
        page.on("pageerror", lambda err, n=name: print(f"{n} PAGEERROR {err}", flush=True))
        page.on(
            "console",
            lambda msg, n=name: print(f"{n} CONSOLE {msg.type} {msg.text[:200]}", flush=True)
            if msg.type in ("error", "warning")
            else None,
        )

    a.goto(url, wait_until="domcontentloaded")
    b.goto(url, wait_until="domcontentloaded")
    a.wait_for_function("() => window.__relay && window.__relay.state.peerReady === true", timeout=45000)
    b.wait_for_function("() => window.__relay && window.__relay.state.peerReady === true", timeout=45000)
    print("PASS peer ready", flush=True)

    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    started = time.time()
    a.click("#sendBtn")
    print("INFO send started", flush=True)

    for i in range(8):
        time.sleep(5)
        sa = a.evaluate(STATE_JS)
        sb = b.evaluate(STATE_JS)
        print(f"--- t={time.time()-started:.0f}s ---", flush=True)
        print(f"A transfers: {sa['transfers']}", flush=True)
        print(f"B transfers: {sb['transfers']}", flush=True)
        if i in (1, 7):
            print("A logs:", flush=True)
            for line in sa["logs"]:
                print("  ", line, flush=True)
            print("B logs:", flush=True)
            for line in sb["logs"]:
                print("  ", line, flush=True)
        recv = [t for t in (sb["transfers"] or []) if t["direction"] == "recv"]
        if recv and recv[0]["state"] in ("received", "failed"):
            break

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


