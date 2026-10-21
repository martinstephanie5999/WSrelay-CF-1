"""Phase 4: UI state machine + responsive screenshots.

Checks waiting/transferring/paused/reconnecting(700ms grace)/done copy at
375px and 1280px, and writes screenshots to test/shots/.

Usage: python phase4-ui.py [BASE]
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
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SHOTS = Path(__file__).resolve().parent / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / "ui-32m.bin"
if not FILE.exists() or FILE.stat().st_size != 32 * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(32):
            f.write(block)

STATUS = "() => document.getElementById('statusText').textContent"

tmp_a = tempfile.mkdtemp(prefix="relay-a-")
tmp_b = tempfile.mkdtemp(prefix="relay-b-")
with sync_playwright() as p:
    mobile = p.chromium.launch_persistent_context(
        tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, viewport={"width": 375, "height": 812}
    )
    desktop = p.chromium.launch_persistent_context(
        tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, viewport={"width": 1280, "height": 900}
    )
    a = mobile.pages[0] if mobile.pages else mobile.new_page()
    b = desktop.pages[0] if desktop.pages else desktop.new_page()

    # Desktop join page must be horizontally centered, not pinned left.
    probe = desktop.new_page()
    probe.goto(BASE + "/", wait_until="domcontentloaded")
    box = probe.evaluate(
        """() => {
          const r = document.getElementById('joinView').getBoundingClientRect();
          return { center: r.left + r.width / 2, viewport: window.innerWidth };
        }"""
    )
    probe.screenshot(path=str(SHOTS / "1280-join.png"), full_page=True)
    probe.close()
    if abs(box["center"] - box["viewport"] / 2) > 8:
        raise AssertionError(f"join view not centered on desktop: {box}")
    print("PASS desktop join view centered", flush=True)

    a.goto(BASE + "/", wait_until="domcontentloaded")
    a.screenshot(path=str(SHOTS / "375-join.png"), full_page=True)
    a.click("#createBtn")
    a.wait_for_function("() => Boolean(window.__relay && window.__relay.state.roomId)", timeout=15000)
    room = a.evaluate("() => window.__relay.state.roomId")
    url = f"{BASE}/r/{room}?auto=1"
    a.wait_for_timeout(800)
    a.screenshot(path=str(SHOTS / "375-waiting.png"), full_page=True)
    if "等待对端" not in a.inner_text("#statusText"):
        raise AssertionError(f"waiting copy missing: {a.inner_text('#statusText')}")

    # Theme toggle: either direction must flip tokens, persist the choice, and
    # we capture a light-mode screenshot regardless of the system default.
    theme0 = a.evaluate("() => document.documentElement.dataset.theme")
    bg0 = a.evaluate("() => getComputedStyle(document.body).backgroundColor")
    a.click("#themeBtn")
    theme1 = a.evaluate("() => document.documentElement.dataset.theme")
    bg1 = a.evaluate("() => getComputedStyle(document.body).backgroundColor")
    if theme1 == theme0 or bg1 == bg0:
        raise AssertionError(f"theme toggle failed: {theme0}->{theme1}, {bg0}->{bg1}")
    if a.evaluate("() => localStorage.getItem('relay.theme')") != theme1:
        raise AssertionError("theme preference not persisted")
    if theme1 != "light":
        a.click("#themeBtn")
    a.wait_for_timeout(300)
    if a.evaluate("() => document.documentElement.dataset.theme") != "light":
        raise AssertionError("could not reach light theme for screenshot")
    a.screenshot(path=str(SHOTS / "375-light.png"), full_page=True)
    if a.evaluate("() => document.documentElement.dataset.theme") != "dark":
        a.click("#themeBtn")
    print("PASS theme toggle + persistence", flush=True)

    b.goto(url, wait_until="domcontentloaded")
    a.wait_for_function("() => window.__relay.state.peerReady === true", timeout=30000)
    b.wait_for_function("() => window.__relay.state.peerReady === true", timeout=30000)

    # Slow the receiver enough to observe the transferring state for a while.
    b.evaluate("() => window.__relay.setWriteDelay(120)")
    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")
    b.wait_for_function(
        "() => { const t = window.__relay.getTransfers().find(x => x.direction === 'recv'); return t && t.receivedBytes > 4 * 1024 * 1024; }",
        timeout=60000,
    )
    a.wait_for_timeout(400)
    a.screenshot(path=str(SHOTS / "375-transferring.png"), full_page=True)
    b.screenshot(path=str(SHOTS / "1280-transferring.png"), full_page=True)
    txt = a.inner_text("#statusText")
    if "不落盘" not in txt and "接收" not in txt:
        raise AssertionError(f"transferring copy missing: {txt}")

    # Pause state copy.
    tid = a.evaluate("() => window.__relay.getTransfers().find(t => t.direction === 'send').transferId")
    a.evaluate("(tid) => window.__relay.pause(tid)", tid)
    a.wait_for_timeout(600)
    if "暂停" not in a.inner_text("#statusText"):
        raise AssertionError(f"paused copy missing: {a.inner_text('#statusText')}")
    print("PASS paused copy", flush=True)
    a.evaluate("(tid) => window.__relay.resume(tid)", tid)

    # 700ms reconnect grace: pause auto-reconnect so the outage lasts beyond
    # the grace; closing the socket must not flash the reconnecting copy
    # before 700ms, but must show it afterwards.
    a.evaluate("() => window.__relay.setReconnectPaused(true)")
    a.evaluate("() => window.__relay.state.ws.close(1000, 'test')")
    a.wait_for_timeout(250)
    early = a.inner_text("#statusText")
    if "正在重新连接" in early:
        raise AssertionError(f"reconnect flashed before 700ms: {early}")
    a.wait_for_timeout(900)
    late = a.inner_text("#statusText")
    if "正在重新连接" not in late and "断点" not in late:
        raise AssertionError(f"reconnect copy missing after 700ms: {late}")
    print("PASS 700ms grace", flush=True)
    a.evaluate("() => window.__relay.setReconnectPaused(false)")

    b.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'recv' && t.state === 'received')",
        timeout=120000,
    )
    a.wait_for_function(
        "() => window.__relay.getTransfers().some(t => t.direction === 'send' && t.state === 'completed')",
        timeout=120000,
    )
    a.wait_for_timeout(800)
    done = a.inner_text("#statusText")
    if "通道已释放" not in done:
        raise AssertionError(f"done copy missing: {done}")
    a.screenshot(path=str(SHOTS / "375-done.png"), full_page=True)
    b.screenshot(path=str(SHOTS / "1280-done.png"), full_page=True)
    if b.evaluate("() => document.documentElement.dataset.theme") != "light":
        b.click("#themeBtn")
    b.wait_for_timeout(300)
    b.screenshot(path=str(SHOTS / "1280-light.png"), full_page=True)
    if b.evaluate("() => document.documentElement.dataset.theme") != "dark":
        b.click("#themeBtn")
    print("PASS done copy", flush=True)

    # Numeric font must be the shipped JetBrains Mono, not an OS fallback.
    a.evaluate("() => document.fonts.ready")
    if not a.evaluate("() => document.fonts.check('12px \"JetBrains Mono\"')"):
        raise AssertionError("JetBrains Mono did not load")
    mono_family = a.evaluate(
        "() => getComputedStyle(document.querySelector('.mono')).fontFamily"
    )
    if "JetBrains Mono" not in mono_family:
        raise AssertionError(f"mono stack wrong: {mono_family}")
    print("PASS JetBrains Mono loaded", flush=True)

    # Mobile layout sanity: no horizontal overflow, buttons tappable.
    overflow = a.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if overflow > 1:
        raise AssertionError(f"horizontal overflow at 375px: {overflow}px")
    for selector in ("#sendBtn", "#shareBtn", "#themeBtn", "#clearReceivedBtn"):
        box = a.locator(selector).bounding_box()
        if not box or box["height"] < 32:
            raise AssertionError(f"tiny tap target: {selector} {box}")
    print("PASS mobile layout", flush=True)

    print(f"INFO screenshots in {SHOTS}", flush=True)
    mobile.close()
    desktop.close()
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)


