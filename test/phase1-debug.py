import json
import os
import secrets
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://relay-min.fvlb2n3o6i.workers.dev"
SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 1  # MiB

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / f"debug-{SIZE}m.bin"
if not FILE.exists() or FILE.stat().st_size != SIZE * 1024 * 1024:
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(SIZE):
            f.write(block)

room = secrets.token_urlsafe(16)
url = f"{BASE}/r/{room}?auto=1"
print(f"INFO room={room} url={url} file={FILE}", flush=True)

STATE_JS = """() => ({
  ready: window.__relay ? window.__relay.state.peerReady : null,
  peer: window.__relay ? {...window.__relay.state.peer} : null,
  transfers: window.__relay ? window.__relay.getTransfers() : null,
  logs: window.__relay ? window.__relay.logs().slice(-12) : null,
})"""

with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True)
    ctx_a = browser.new_context()
    ctx_b = browser.new_context()
    a = ctx_a.new_page()
    b = ctx_b.new_page()

    for name, page in (("A", a), ("B", b)):
        page.on("pageerror", lambda err, n=name: print(f"{n} PAGEERROR {err}", flush=True))
        page.on("console", lambda msg, n=name: print(f"{n} CONSOLE {msg.type} {msg.text}", flush=True))

        def attach_ws(ws, n=name):
            print(f"{n} WS {ws.url}", flush=True)

            def on_sent(payload, n=n):
                if isinstance(payload, bytes):
                    print(f"{n} WS-SENT binary {len(payload)}B", flush=True)
                else:
                    print(f"{n} WS-SENT text {payload[:160]}", flush=True)

            def on_recv(payload, n=n):
                if isinstance(payload, bytes):
                    print(f"{n} WS-RECV binary {len(payload)}B", flush=True)
                else:
                    print(f"{n} WS-RECV text {payload[:160]}", flush=True)

            ws.on("framesent", on_sent)
            ws.on("framereceived", on_recv)

        page.on("websocket", attach_ws)

    a.goto(url, wait_until="domcontentloaded")
    b.goto(url, wait_until="domcontentloaded")
    a.wait_for_function("() => Boolean(window.__relay)", timeout=20000)
    b.wait_for_function("() => Boolean(window.__relay)", timeout=20000)

    for _ in range(30):
        ra = a.evaluate(STATE_JS)
        rb = b.evaluate(STATE_JS)
        if ra["ready"] and rb["ready"]:
            break
        time.sleep(1)
    print("READY A", a.evaluate("() => window.__relay.state.peerReady"), flush=True)
    print("READY B", b.evaluate("() => window.__relay.state.peerReady"), flush=True)

    a.set_input_files("#fileInput", str(FILE))
    a.wait_for_function("() => !document.getElementById('sendBtn').disabled", timeout=15000)
    a.click("#sendBtn")
    print("SENT", flush=True)

    for i in range(40):
        time.sleep(1)
        ra = a.evaluate(STATE_JS)
        rb = b.evaluate(STATE_JS)
        print(f"--- t={i+1}s ---", flush=True)
        print("A", json.dumps(ra, ensure_ascii=False), flush=True)
        print("B", json.dumps(rb, ensure_ascii=False), flush=True)
        a_done = ra["transfers"] and any(
            t["direction"] == "send" and t["state"] in ("completed", "failed")
            for t in ra["transfers"]
        )
        b_done = rb["transfers"] and any(
            t["direction"] == "recv" and t["state"] in ("received", "failed")
            for t in rb["transfers"]
        )
        if a_done and b_done:
            break

    browser.close()


