import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
SIZE_MIB = int(sys.argv[2]) if len(sys.argv) > 2 else 100
DEBUG_TOKEN = os.environ.get("DEBUG_TOKEN")
LAUNCH_ARGS = ["--no-proxy-server"] if os.environ.get("RELAY_DIRECT") == "1" else []

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / f"phase1-{SIZE_MIB}m.bin"


def ensure_file():
    if FILE.exists() and FILE.stat().st_size == SIZE_MIB * 1024 * 1024:
        return
    block = os.urandom(1024 * 1024)
    with open(FILE, "wb") as f:
        for _ in range(SIZE_MIB):
            f.write(block)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def seg_hashes(path, seg=64 * 1024 * 1024):
    out = []
    with open(path, "rb") as f:
        while True:
            block = f.read(seg)
            if not block:
                break
            out.append(hashlib.sha256(block).hexdigest())
    return out


def debug_storage(room):
    req = urllib.request.Request(
        f"{BASE}/__debug/storage/{room}",
        headers={
            "x-debug-token": DEBUG_TOKEN,
            "accept": "application/json",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36"
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_peer(page, label):
    page.wait_for_function(
        "() => window.__relay && window.__relay.state.peerReady === true",
        timeout=45000,
    )
    print(f"PASS {label}: peer ready")


def send_file(page, path, label):
    page.set_input_files("#fileInput", str(path))
    page.wait_for_function(
        "() => !document.getElementById('sendBtn').disabled",
        timeout=15000,
    )
    page.click("#sendBtn")
    print(f"INFO {label}: send started")


def dump_page(page, label):
    try:
        transfers = page.evaluate(
            "() => window.__relay ? window.__relay.getTransfers() : null"
        )
        print(f"DUMP {label} transfers: {json.dumps(transfers, ensure_ascii=False)}", flush=True)
        logs = page.evaluate(
            "() => window.__relay ? window.__relay.logs().slice(-25) : []"
        )
        print(f"DUMP {label} logs:", flush=True)
        for line in logs:
            print("  ", line, flush=True)
    except Exception as exc:
        print(f"WARN {label} dump failed: {exc}", flush=True)


def wait_recv(page, label, timeout=600000, peer=None):
    try:
        page.wait_for_function(
            """() => window.__relay.getTransfers().some(
                 t => t.direction === 'recv' && t.state === 'received')""",
            timeout=timeout,
        )
    except Exception:
        dump_page(page, label)
        if peer is not None:
            dump_page(peer, label + "-peer")
        raise
    print(f"PASS {label}: receive complete")


def wait_send(page, label, timeout=600000, peer=None):
    try:
        page.wait_for_function(
            """() => window.__relay.getTransfers().some(
                 t => t.direction === 'send' && t.state === 'completed')""",
            timeout=timeout,
        )
    except Exception:
        dump_page(page, label)
        if peer is not None:
            dump_page(peer, label + "-peer")
        raise
    print(f"PASS {label}: sender complete")


def main():
    ensure_file()
    source_hash = sha256_file(FILE)
    room = secrets.token_urlsafe(16)
    url = f"{BASE}/r/{room}?auto=1"
    print(f"INFO file={FILE} size={FILE.stat().st_size} hash={source_hash}")
    print(f"INFO room={room} url={url}")

    tmp_a = tempfile.mkdtemp(prefix="relay-a-")
    tmp_b = tempfile.mkdtemp(prefix="relay-b-")
    with sync_playwright() as p:
        # Incognito contexts keep OPFS memory-backed and hit short writes on
        # large files; persistent contexts are disk-backed like a real profile.
        ctx_a = p.chromium.launch_persistent_context(
            tmp_a, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, args=LAUNCH_ARGS
        )
        ctx_b = p.chromium.launch_persistent_context(
            tmp_b, channel=os.environ.get("RELAY_BROWSER", "msedge"), headless=True, args=LAUNCH_ARGS
        )
        a = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
        b = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()

        errors = []
        for name, page in (("A", a), ("B", b)):
            page.on("pageerror", lambda err, n=name: errors.append(f"{n} pageerror: {err}"))
            page.on(
                "console",
                lambda msg, n=name: errors.append(f"{n} console.{msg.type}: {msg.text}")
                if msg.type == "error"
                else None,
            )

        a.goto(url, wait_until="domcontentloaded")
        b.goto(url, wait_until="domcontentloaded")
        a.wait_for_function("() => Boolean(window.__relay)", timeout=20000)
        b.wait_for_function("() => Boolean(window.__relay)", timeout=20000)
        wait_peer(a, "A")
        wait_peer(b, "B")

        started = time.time()
        send_file(a, FILE, "A->B")

        wait_recv(b, "A->B", peer=a)
        wait_send(a, "A->B", peer=b)
        elapsed = time.time() - started

        recv = b.evaluate(
            "() => window.__relay.getTransfers().filter(t => t.direction === 'recv')"
        )
        if len(recv) != 1:
            raise AssertionError(f"expected one recv transfer, got {recv}")
        t = recv[0]
        file_id = t["fileId"]
        received_size = b.evaluate(
            "(fid) => window.__relay.getReceivedSize(fid)", file_id
        )
        print(f"INFO received size={received_size}")
        print(f"INFO A->B elapsed={elapsed:.1f}s ({SIZE_MIB / elapsed:.1f} MiB/s)")
        if received_size != FILE.stat().st_size:
            raise AssertionError(f"size mismatch: {received_size} != {FILE.stat().st_size}")
        if FILE.stat().st_size > 256 * 1024 * 1024:
            # The page skips the whole-file digest above 256 MiB; compare
            # 64 MiB segment hashes instead (same coverage, split).
            seg = 64 * 1024 * 1024
            got = b.evaluate(
                "(args) => window.__relay.getReceivedSegmentHashes(args[0], args[1])",
                [file_id, seg],
            )
            want = seg_hashes(FILE, seg)
            if len(got) != len(want):
                raise AssertionError(f"segment count mismatch: {len(got)} != {len(want)}")
            bad = [i for i, (g, w) in enumerate(zip(got, want)) if g != w]
            if bad:
                raise AssertionError(f"segment hash mismatch at {bad[:5]}")
            print(f"PASS A->B: size and {len(want)}x64MiB segment hashes match")
        else:
            received_hash = b.evaluate(
                "(fid) => window.__relay.getReceivedHash(fid)", file_id
            )
            print(f"INFO received hash={received_hash}")
            if received_hash != source_hash:
                raise AssertionError(f"hash mismatch: {received_hash} != {source_hash}")
            print("PASS A->B: size and SHA-256 match")

        # Storage audit: no file bytes or transfer state may be persisted.
        # If DEBUG_TOKEN is set, the audit is mandatory: a 403/404 means the
        # token or deployment is wrong, not that the check can be skipped.
        if not DEBUG_TOKEN:
            print("SKIP storage audit: DEBUG_TOKEN not set")
            debug = None
        else:
            try:
                debug = debug_storage(room)
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 404):
                    raise AssertionError(
                        "DEBUG_TOKEN is set but the debug endpoint rejected it"
                    ) from exc
                raise
        if debug is not None:
            print(f"INFO debug storage={json.dumps(debug, ensure_ascii=False)}")
            if debug.get("storageKeys"):
                raise AssertionError(f"server storage is not empty: {debug['storageKeys']}")
            for socket in debug.get("sockets", []):
                extra = set(socket.get("attachmentKeys", [])) - {
                    "roomId", "clientId", "connId", "joinedAt", "earlyData",
                    "serverNonce", "rejected", "greeted", "announced",
                    "authChecked", "authed",
                }
                if extra:
                    raise AssertionError(f"unexpected attachment keys: {extra}")
            print("PASS storage audit: no server-side file/transfer state")

        # Reverse direction: B -> A with a smaller file.
        small = DATA_DIR / "phase1-reverse.bin"
        if not small.exists() or small.stat().st_size != 8 * 1024 * 1024:
            block = os.urandom(1024 * 1024)
            with open(small, "wb") as f:
                for _ in range(8):
                    f.write(block)
        small_hash = sha256_file(small)

        send_file(b, small, "B->A")
        wait_recv(a, "B->A", peer=b)
        wait_send(b, "B->A", peer=a)
        recv_a = a.evaluate(
            "() => window.__relay.getTransfers().filter(t => t.direction === 'recv')"
        )
        if len(recv_a) != 1:
            raise AssertionError(f"expected one recv transfer on A, got {recv_a}")
        t2 = recv_a[0]
        hash2 = a.evaluate(
            "(fid) => window.__relay.getReceivedHash(fid)", t2["fileId"]
        )
        if hash2 != small_hash:
            raise AssertionError(f"reverse hash mismatch: {hash2} != {small_hash}")
        print("PASS B->A: size and SHA-256 match")

        if errors:
            print("ERRORS:")
            for e in errors:
                print(" ", e)
        else:
            print("PASS no browser errors")

        # Recent client logs help correlate connection drops with the DO logs.
        for name, page in (("A", a), ("B", b)):
            try:
                logs = page.evaluate("() => window.__relay.logs().slice(-15)")
                print(f"INFO {name} last logs:", flush=True)
                for line in logs:
                    print("  ", line, flush=True)
            except Exception as exc:
                print(f"WARN {name} logs unavailable: {exc}", flush=True)

        ctx_a.close()
        ctx_b.close()
    shutil.rmtree(tmp_a, ignore_errors=True)
    shutil.rmtree(tmp_b, ignore_errors=True)


if __name__ == "__main__":
    main()


