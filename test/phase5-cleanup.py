"""Cleanup regression for S13.

Part A: completed send rows can be cleared; completed receive rows can be
cleared from both UI and OPFS.
Part B: an interrupted receive survives reload as a "partial" row and can be
deleted (row + OPFS data/meta).

Usage: python phase5-cleanup.py [BASE]
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

FILE1 = DATA_DIR / "cleanup-1m.bin"
if not FILE1.exists() or FILE1.stat().st_size != 1024 * 1024:
    FILE1.write_bytes(os.urandom(1024 * 1024))
FILE2 = DATA_DIR / "cleanup-4m.bin"
if not FILE2.exists() or FILE2.stat().st_size != 4 * 1024 * 1024:
    FILE2.write_bytes(os.urandom(4 * 1024 * 1024))

room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}?auto=1"
print(f"INFO room={room} url={url}", flush=True)

SEND_JS = """() => window.__relay.getTransfers().find(t => t.direction === 'send')"""
RECV_JS = """() => window.__relay.getTransfers().find(t => t.direction === 'recv')"""

OPFS_NAMES_JS = """
async (room) => {
  const root = await navigator.storage.getDirectory();
  const relay = await root.getDirectoryHandle("relay");
  const safe = room.replace(/[^A-Za-z0-9_-]/g, "_") || "room";
  const dir = await relay.getDirectoryHandle(safe);
  const names = [];
  for await (const [name] of dir.entries()) names.push(name);
  return names;
}
"""

tmp_a = tempfile.mkdtemp(prefix="relay-cl-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-cl-b-")
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

    # --- Part A: completed rows cleanup ---
    a.set_input_files("#fileInput", str(FILE1))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")
    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=60000,
    )
    a.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'send' && t.state === 'completed')",
        timeout=60000,
    )
    print("PASS completed transfer", flush=True)

    a.locator("#clearOutgoingBtn").click()
    a.locator("#clearOutgoingBtn").click()
    a.wait_for_function(
        "() => window.__relay.getTransfers().filter(t => t.direction === 'send').length === 0",
        timeout=15000,
    )
    print("PASS cleared completed send rows", flush=True)

    b.locator("#clearReceivedBtn").click()
    b.locator("#clearReceivedBtn").click()
    b.wait_for_function(
        "() => window.__relay.getTransfers().filter(t => t.direction === 'recv').length === 0",
        timeout=15000,
    )
    names = b.evaluate(OPFS_NAMES_JS, room)
    if names:
        raise AssertionError(f"OPFS not empty after clear: {names}")
    print("PASS cleared received rows and OPFS", flush=True)

    # --- Part B: partial row after reload ---
    b.evaluate("() => window.__relay.setWriteDelay(120)")
    a.set_input_files("#fileInput", str(FILE2))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")
    b.wait_for_function(
        "() => { const t = window.__relay.getTransfers().find(x => x.direction === 'recv'); return t && t.receivedBytes > 1024 * 1024 && t.state === 'receiving'; }",
        timeout=30000,
    )
    recv = b.evaluate(RECV_JS)
    fid = recv["fileId"]
    print(f"INFO partial at {recv['receivedBytes']} bytes file={fid}", flush=True)

    # Stop both sides before the receiver can finish: close B first so its
    # meta stays at the last persisted boundary, then close A.
    b.evaluate("() => { window.__relay.setReconnectPaused(true); window.__relay.state.ws.close(); }")
    a.evaluate("() => { window.__relay.setReconnectPaused(true); window.__relay.state.ws.close(); }")
    time.sleep(3.0)
    b.reload(wait_until="domcontentloaded")
    b.wait_for_function("() => Boolean(window.__relay)", timeout=20000)
    try:
        b.wait_for_function(
            "(fid) => window.__relay.getTransfers().some(t => t.fileId === fid && t.state === 'partial')",
            arg=fid,
            timeout=20000,
        )
    except Exception:
        debug = b.evaluate("""
          async (room) => {
            const root = await navigator.storage.getDirectory();
            const relay = await root.getDirectoryHandle("relay");
            const dir = await relay.getDirectoryHandle(room.replace(/[^A-Za-z0-9_-]/g, "_"));
            const names = [];
            const metas = [];
            for await (const [name] of dir.entries()) names.push(name);
            for (const name of names.filter((n) => n.includes(".meta."))) {
              try {
                const fh = await dir.getFileHandle(name);
                metas.push([name, (await (await fh.getFile()).text()).slice(0, 180)]);
              } catch {}
            }
            return {
              names,
              metas,
              transfers: window.__relay.getTransfers(),
              logs: window.__relay.logs().slice(-20),
              roomId: window.__relay.state.roomId,
              clientId: localStorage.getItem("relay.clientId"),
            };
          }
        """, room)
        print("DEBUG partial restore:", debug, flush=True)
        raise
    partial = b.evaluate("(fid) => window.__relay.getTransfers().find(t => t.fileId === fid)", fid)
    if partial["state"] != "partial":
        raise AssertionError(f"expected partial, got {partial}")
    if not b.locator("#incoming .item .remove").first.is_visible():
        raise AssertionError("partial remove button not visible")
    print("PASS partial row restored after reload", flush=True)

    b.locator("#incoming .item .remove").first.click()
    b.wait_for_function(
        "(fid) => !window.__relay.getTransfers().some(t => t.fileId === fid)",
        arg=fid,
        timeout=15000,
    )
    names = b.evaluate(OPFS_NAMES_JS, room)
    if any(fid in n for n in names):
        raise AssertionError(f"partial files not deleted: {names}")
    print("PASS partial row and OPFS files deleted", flush=True)

    if errors:
        raise AssertionError(f"page errors: {errors}")
    print("PASS no page errors", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)

