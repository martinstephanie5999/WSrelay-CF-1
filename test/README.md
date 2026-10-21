# Test suite

All browser tests use Edge by default. Override with `RELAY_BROWSER`:

```powershell
$env:RELAY_BROWSER = "chromium"   # or "msedge", "chrome"
python test/phase1.py http://127.0.0.1:8787 4
```

Start a local server first:

```powershell
wrangler dev --config .\wrangler.toml --port 8787 --local --var DEBUG_TOKEN:local
```

## Receive confirmation

The client now asks before writing an incoming file. Tests that are not about
that prompt append `?auto=1` to the room URL. To exercise the prompt, use
`phase5-receive-confirm.py` / `phase5-batch-confirm.py` and click the
`接收` / `本批全部接收` buttons.

## Test map

| File | Purpose |
| --- | --- |
| `phase1.py` | bidirectional transfer + storage audit |
| `phase1-loss.py` | dropped binary frame + retransmit |
| `phase2-*.py` | ACK/complete loss, pause, backpressure, throughput |
| `phase3-*.py` | reload and reconnect resume |
| `phase4-*.py` | password, QR, UI |
| `phase5-batch*.py` | batch transfer / batch confirmation |
| `phase5-cleanup.py` | completed-row and partial-file cleanup |
| `phase5-receive-confirm.py` | accept/reject an offer |
| `phase5-room-full.py` | third connection gets 4003 |
| `phase5-forged-presence.py` | nonce-bound server presence |
| `phase5-unauth-presence.py` | unauthenticated sockets cannot displace peers |
| `phase5-pause-ack-loss.py` | pause + lost ACK |
| `phase5-long-offline.py` | offline >3s then resume |
