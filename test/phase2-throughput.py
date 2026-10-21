"""Throughput probe: runs one A->B transfer and samples progress every 5s.

Prints per-interval receiver rate plus cumulative persist timings
(close/meta/reopen) so a slowdown can be attributed to the persistence path.

Usage: python phase2-throughput.py [BASE] [SIZE_MIB]
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
SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 256

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / f"throughput-{SIZE}m.bin"
if not FILE.exists() or FILE.stat().st_size != SIZE * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(SIZE):
            f.write(block)

room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}?auto=1"
print(f"INFO room={room} size={SIZE}MiB", flush=True)

STATE_JS = """() => window.__relay.getTransfers().map(t => ({
  d: t.direction, s: t.state, sent: t.sentOffset, acked: t.ackedOffset,
  recv: t.receivedBytes, writeMs: t.writeMs, flushMs: t.flushMs,
  metaMs: t.metaMs, err: t.error }))"""

tmp_a = tempfile.mkdtemp(prefix="relay-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-b-")
with sync_playwright() as p:
    # Persistent contexts: disk-backed OPFS, matching a real browser profile.
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
    started = time.time()
    prev_recv = 0
    a.click("#sendBtn")
    print("INFO send started", flush=True)

    deadline = started + 25 * 60
    while time.time() < deadline:
        time.sleep(5)
        try:
            sa = a.evaluate(STATE_JS)
            sb = b.evaluate(STATE_JS)
        except Exception as exc:
            print(f"WARN state unavailable: {exc}", flush=True)
            break
        send_t = next((t for t in sa if t["d"] == "send"), None)
        recv_t = next((t for t in sb if t["d"] == "recv"), None)
        if not send_t or not recv_t:
            continue
        recv = recv_t["recv"]
        rate = (recv - prev_recv) / 5 / 1024 / 1024
        prev_recv = recv
        print(
            f"T+{time.time()-started:6.1f}s recv={recv/1024/1024:7.1f}MiB "
            f"({rate:5.2f} MiB/s) sender={send_t['s']} "
            f"acked={send_t['acked']/1024/1024:7.1f}MiB "
            f"write={recv_t['writeMs']:6d}ms flush={recv_t['flushMs']:5d}ms "
            f"meta={recv_t['metaMs']:4d}ms"
            + (f" err={send_t['err'] or recv_t['err']}" if (send_t.get("err") or recv_t.get("err")) else ""),
            flush=True,
        )
        if send_t["s"] in ("completed", "failed") and recv_t["s"] in ("received", "failed"):
            break
    elapsed = time.time() - started
    print(f"INFO done in {elapsed:.1f}s", flush=True)
    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


