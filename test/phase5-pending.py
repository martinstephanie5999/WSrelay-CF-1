"""Phase 5: pending queue + durable receive list.

Covers two reported issues:
  1. selected files were invisible until transfer start -> pending queue rows
  2. completed receives vanished on reload -> list rebuilt from OPFS .meta

Usage: python phase5-pending.py [BASE]
"""

import os
import shutil
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
LAUNCH_ARGS = ["--no-proxy-server"] if os.environ.get("RELAY_DIRECT") == "1" else []

MAKE_FILES = """(names) => {
  const dt = new DataTransfer();
  for (const n of names) dt.items.add(new File([new Uint8Array(1024 * 1024)], n));
  const input = document.getElementById('fileInput');
  input.files = dt.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
}"""

tmp_a = tempfile.mkdtemp(prefix="relay-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-b-")
with sync_playwright() as p:
    ctx_a = p.chromium.launch_persistent_context(tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, args=LAUNCH_ARGS)
    ctx_b = p.chromium.launch_persistent_context(tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, args=LAUNCH_ARGS)
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

    # --- 1. pending queue is visible before sending
    a.evaluate(MAKE_FILES, ["pending-1.bin", "pending-2.bin"])
    if a.is_hidden("#pendingBox"):
        raise AssertionError("pending box still hidden after selecting files")
    rows = a.locator("#pendingList .pending-row")
    if rows.count() != 2:
        raise AssertionError(f"expected 2 pending rows, got {rows.count()}")
    summary = a.inner_text("#pendingSummary")
    if "2 个文件" not in summary:
        raise AssertionError(f"pending summary wrong: {summary}")
    if "（2）" not in a.inner_text("#sendBtn"):
        raise AssertionError(f"send button does not show the count: {a.inner_text('#sendBtn')}")
    names = a.evaluate(
        "() => [...document.querySelectorAll('#pendingList .p-name')].map(e => e.textContent)"
    )
    if names != ["pending-1.bin", "pending-2.bin"]:
        raise AssertionError(f"pending names wrong: {names}")
    print("PASS pending queue lists selected files", flush=True)

    # Removing one pending row must update the summary and the count.
    a.locator("#pendingList .pending-row", has_text="pending-2.bin").locator(".p-remove").click()
    if a.locator("#pendingList .pending-row").count() != 1:
        raise AssertionError("remove did not drop the row")
    if "1 个文件" not in a.inner_text("#pendingSummary"):
        raise AssertionError("summary did not follow removal")
    print("PASS pending row removal", flush=True)

    a.click("#sendBtn")
    if not a.is_hidden("#pendingBox"):
        raise AssertionError("pending box should clear once the batch starts")
    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=90000,
    )
    a.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'send' && t.state === 'completed')",
        timeout=90000,
    )
    print("PASS pending batch sent", flush=True)

    # --- 2. a second transfer still lands in the receive list
    a.evaluate(MAKE_FILES, ["second.bin"])
    a.click("#sendBtn")
    b.wait_for_function(
        "() => window.__relay.getTransfers().filter(t => t.direction === 'recv' && t.state === 'received').length >= 2",
        timeout=90000,
    )
    b.wait_for_timeout(400)
    if b.locator("#incoming .recv-item").count() != 2:
        raise AssertionError("second receive row missing")
    print("PASS second transfer listed while page is live", flush=True)

    # --- 3. the receive list survives a reload (OPFS .meta rebuild)
    b.reload(wait_until="domcontentloaded")
    b.wait_for_function(
        "() => window.__relay && window.__relay.getTransfers().filter(t => t.direction === 'recv' && t.state === 'received').length === 2",
        timeout=30000,
    )
    b.wait_for_timeout(300)
    rows = b.locator("#incoming .recv-item")
    if rows.count() != 2:
        raise AssertionError(f"restored rows wrong: {rows.count()}")
    restored_names = b.evaluate(
        "() => [...document.querySelectorAll('#incoming .recv-item .name')].map(e => e.textContent)"
    )
    if sorted(restored_names) != ["pending-1.bin", "second.bin"]:
        raise AssertionError(f"restored names wrong: {restored_names}")
    if b.locator("#incoming .save:not(.hidden)").count() != 2:
        raise AssertionError("restored rows must keep their save button")
    if "2 个文件" not in b.inner_text("#recvCount"):
        raise AssertionError(f"receive count not restored: {b.inner_text('#recvCount')}")
    status = b.inner_text("#statusText")
    if "通道已释放" in status:
        raise AssertionError(f"restored rows must not fake a live completion: {status}")
    print(f"PASS receive list restored after reload (status: {status})", flush=True)

    # A fresh transfer after the reload must still append to the restored list.
    a.evaluate(MAKE_FILES, ["third.bin"])
    a.click("#sendBtn")
    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.path === 'third.bin' && t.state === 'received')",
        timeout=90000,
    )
    b.wait_for_timeout(400)
    if b.locator("#incoming .recv-item").count() != 3:
        raise AssertionError("new receive row missing after restore")
    print("PASS new receive appended after restore", flush=True)

    # --- 4. clearing received files deletes OPFS copies and the rows
    if b.locator("#clearReceivedBtn").is_disabled():
        raise AssertionError("clear button should be enabled with received files")
    b.locator("#clearReceivedBtn").click()
    b.wait_for_timeout(200)
    if b.evaluate("() => document.querySelectorAll('#incoming .recv-item').length") != 3:
        raise AssertionError("first click must only ask for confirmation")
    if b.evaluate("() => document.getElementById('clearReceivedBtn').textContent") != "确认清空？":
        raise AssertionError("missing confirmation state")
    b.locator("#clearReceivedBtn").click()
    b.wait_for_function(
        "() => window.__relay.getTransfers().filter(t => t.direction === 'recv').length === 0",
        timeout=10000,
    )
    b.wait_for_timeout(300)
    if b.evaluate("() => document.querySelectorAll('#incoming .recv-item').length") != 0:
        raise AssertionError("rows remain after clearing")
    if not b.evaluate("() => document.getElementById('clearReceivedBtn').disabled"):
        raise AssertionError("clear button should disable when the list is empty")
    if b.evaluate("() => document.getElementById('recvCount').textContent") != "":
        raise AssertionError("receive count not reset")

    opfs_entries = b.evaluate(
        """async () => {
          const root = await navigator.storage.getDirectory();
          const relay = await root.getDirectoryHandle('relay');
          const name = window.__relay.state.roomId.replace(/[^A-Za-z0-9_-]/g, '_');
          const room = await relay.getDirectoryHandle(name);
          let count = 0;
          for await (const entry of room.entries()) count += 1;
          return count;
        }"""
    )
    if opfs_entries != 0:
        raise AssertionError(f"OPFS still holds {opfs_entries} entries after clearing")
    print("PASS clear received files removes rows and OPFS entries", flush=True)

    # Receiving must still work after the cleanup.
    a.evaluate(MAKE_FILES, ["after-clear.bin"])
    a.click("#sendBtn")
    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.path === 'after-clear.bin' && t.state === 'received')",
        timeout=90000,
    )
    b.wait_for_timeout(300)
    if b.evaluate("() => document.querySelectorAll('#incoming .recv-item').length") != 1:
        raise AssertionError("receive after clearing failed")
    print("PASS receive still works after clearing", flush=True)

    ctx_a.close()
    ctx_b.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


