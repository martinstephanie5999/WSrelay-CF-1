"""Phase 4: the share QR must encode exactly the room link.

Renders the page's exported QR matrix, decodes it with jsqr (dev tool), and
compares against the share link. Also asserts the password never enters the
QR payload.

Usage: python phase4-qr.py [BASE]
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
HERE = Path(__file__).resolve().parent
JSQR_DIR = Path(os.environ["TEMP"]) / "qrcheck" / "node_modules" / "jsqr"
if not JSQR_DIR.exists():
    raise SystemExit(f"jsqr not installed at {JSQR_DIR}")

tmp_a = tempfile.mkdtemp(prefix="relay-a-")
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.fill("#passwordInput", "qr-test-password")
    page.click("#createBtn")
    page.wait_for_function("() => Boolean(window.__relay && window.__relay.state.roomId)", timeout=15000)
    link = page.evaluate("() => window.__relay.getShareLink()")
    matrix = page.evaluate("() => window.__relay.getQrMatrix()")
    if not matrix:
        raise AssertionError("no QR matrix exported")
    if "qr-test-password" in json.dumps(matrix) or "qr-test-password" in link:
        raise AssertionError("password leaked into link/QR")

    payload = Path(tempfile.mkstemp(suffix=".json")[1])
    payload.write_text(json.dumps({"matrix": matrix, "link": link}), encoding="utf-8")
    try:
        out = subprocess.run(
            ["node", str(HERE / "qr-decode.mjs"), str(JSQR_DIR), str(payload)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.returncode != 0:
            raise AssertionError(f"QR decode failed: {out.stderr.strip()}")
        print(f"PASS QR decodes to {out.stdout.strip()}", flush=True)
    finally:
        try:
            payload.unlink(missing_ok=True)
        except PermissionError:
            pass
    ctx.close()
shutil.rmtree(tmp_a, ignore_errors=True)

