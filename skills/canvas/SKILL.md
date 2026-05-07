---
name: canvas
description: Open the review-canvas web UI, optionally loading a specific file. Supports JSONL session transcripts and plain .md/.txt files. Hot-reloads the running server via POST /load — no MCP reconnect needed.
---

# Canvas — Load a File into review-canvas

Open the review-canvas web UI pre-loaded with a specific file (transcript or markdown document) for inline commenting.

## Usage

```
/memesis:canvas [file]       # Load <file> into the canvas
/memesis:canvas              # Show currently loaded file
```

## Arguments

- `[file]` — path to a JSONL transcript or `.md`/`.txt` file (absolute or relative to cwd). Supports tilde expansion.

## Procedure

### With a file argument

1. Expand the file path, verify it exists.
2. Try `POST http://localhost:{PORT}/load` — if success, report and done.
3. If connection refused: start the daemon as background process, wait up to 3s for port, retry `POST /load` once.

### Without a file argument

`GET http://localhost:{PORT}/status` — report daemon state (turn count, bridge connection) if up, otherwise report not running.

## Implementation

```python
import json, os, sys, time, subprocess, urllib.request, urllib.error

PORT = int(os.environ.get("REVIEW_CANVAS_PORT", "8788"))
PLUGIN_DIR = os.path.expanduser("~/projects/memesis/tools/channels/review-canvas")
args = "<args>"

def post_load(file_path):
    payload = json.dumps({"path": file_path}).encode()
    req = urllib.request.Request(
        f"http://localhost:{PORT}/load", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=3) as resp:
        return json.loads(resp.read())

def start_daemon():
    subprocess.Popen(
        ["bun", "run", "daemon.ts"],
        cwd=PLUGIN_DIR,
        stdout=open("/tmp/review-canvas-daemon.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    # wait for port to open (up to 3s)
    for _ in range(15):
        try:
            urllib.request.urlopen(f"http://localhost:{PORT}/status", timeout=0.5)
            return True
        except Exception:
            time.sleep(0.2)
    return False

if args.strip():
    file_path = os.path.abspath(os.path.expanduser(args.strip()))
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        sys.exit(1)

    try:
        result = post_load(file_path)
        print(f"Loaded: {file_path}")
        print(f"{result['turns']} turns — http://localhost:{PORT}")
    except (urllib.error.URLError, OSError):
        print("Daemon not running — starting...")
        if start_daemon():
            try:
                result = post_load(file_path)
                print(f"Loaded: {file_path}")
                print(f"{result['turns']} turns — http://localhost:{PORT}")
            except Exception as e:
                print(f"Error: daemon started but /load failed: {e}")
                sys.exit(1)
        else:
            print(f"Error: daemon failed to start. Check /tmp/review-canvas-daemon.log")
            sys.exit(1)
else:
    try:
        req = urllib.request.Request(f"http://localhost:{PORT}/status", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            status = json.loads(resp.read())
        print(f"Daemon running — {status['turns']} turns, bridge {'connected' if status['bridge_connected'] else 'disconnected'}. http://localhost:{PORT}")
    except (urllib.error.URLError, OSError):
        print(f"Daemon not running on port {PORT}.")
```

## Notes

- Hot-reload broadcasts `{type: 'reload', turns: [...]}` over WebSocket — browser re-renders without a page refresh, no MCP reconnect needed.
- Daemon starts automatically if not running — no manual /mcp needed.
- Daemon persists independently of MCP lifecycle. HTTP server stays up across Claude Code restarts.
