"""Phase 5: backpressure status must track the transfer's own ACK cadence.

A full send window waiting for the next 2 MiB persist ACK is normal, so a slow
path must not light up "接收端处理中，暂停发送". Only a silence well beyond the
measured cadence (or a congested socket buffer) counts.

Usage: python phase5-backpressure-ui.py [BASE]
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
FILE = DATA_DIR / "bp-24m.bin"
if not FILE.exists() or FILE.stat().st_size != 24 * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(24):
            f.write(block)
expected = hashlib.sha256(FILE.read_bytes()).hexdigest()

SAMPLER = """() => {
  window.__statusSamples = [];
  window.__statusTimer = setInterval(() => {
    const el = document.getElementById('statusText');
    window.__statusSamples.push(el.textContent);
  }, 200);
}"""
STOP_SAMPLER = """() => {
  clearInterval(window.__statusTimer);
  return window.__statusSamples;
}"""

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
    a.on("pageerror", lambda e: print("A pageerror:", e))
    b.on("pageerror", lambda e: print("B pageerror:", e))
    a.goto(BASE + "/", wait_until="domcontentloaded")
    a.click("#createBtn")
    a.wait_for_function("() => Boolean(window.__relay && window.__relay.state.roomId)", timeout=15000)
    room = a.evaluate("() => window.__relay.state.roomId")
    b.goto(f"{BASE}/r/{room}?auto=1", wait_until="domcontentloaded")
    a.wait_for_function("() => window.__relay.state.peerReady === true", timeout=30000)
    b.wait_for_function("() => window.__relay.state.peerReady === true", timeout=30000)

    # Slow but healthy path: 250 ms per 256 KiB chunk, about 1 MiB/s.
    b.evaluate("() => window.__relay.setWriteDelay(250)")
    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")
    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.receivedBytes > 0)",
        timeout=30000,
    )
    a.evaluate(SAMPLER)
    a.wait_for_timeout(8000)
    samples = a.evaluate(STOP_SAMPLER)
    warn = [s for s in samples if "接收端处理中" in s or "链路拥塞" in s]
    if warn:
        raise AssertionError(f"spurious backpressure on a slow path: {warn[:5]} ({len(warn)}/{len(samples)})")
    print(f"PASS no backpressure warning on a slow path ({len(samples)} samples)", flush=True)

    # Genuine stall: delay each chunk far beyond the established cadence.
    sending = a.evaluate(
        "() => window.__relay.getTransfers().some(t => t.direction === 'send' && t.state === 'sending')"
    )
    if not sending:
        raise AssertionError("transfer already finished before the stall phase")
    b.evaluate("() => window.__relay.setWriteDelay(5000)")
    if b.evaluate("() => window.__relay.state.writeDelayMs") != 5000:
        raise AssertionError("write delay did not apply")
    a.evaluate(SAMPLER)
    try:
        a.wait_for_function(
            """() => window.__statusSamples.some(
                 s => s.includes('接收端处理中') || s.includes('链路拥塞'))""",
            timeout=60000,
        )
    except Exception:
        samples = a.evaluate(STOP_SAMPLER)
        raise AssertionError(f"stall never reported as backpressure: {samples[-10:]}")
    stalled = a.evaluate(STOP_SAMPLER)
    first = next(s for s in stalled if "接收端处理中" in s or "链路拥塞" in s)
    print(f"PASS real stall surfaces as: {first}", flush=True)

    # Recovery: the transfer finishes once the receiver keeps up again.
    b.evaluate("() => window.__relay.setWriteDelay(0)")
    a.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'send' && t.state === 'completed')",
        timeout=180000,
    )
    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=180000,
    )
    recv = b.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'recv')")
    got = b.evaluate("(fid) => window.__relay.getReceivedHash(fid)", recv["fileId"])
    if got != expected:
        raise AssertionError("hash mismatch after recovery")
    print("PASS transfer completed after the stall", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


