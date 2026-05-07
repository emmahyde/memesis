# review-canvas

PR-style transcript review channel for Claude Code.

Drag-select line ranges in a JSONL transcript, attach comments, stream them
into the running Claude Code session as `<channel source="review-canvas">`
events. Claude responds via the `reply` tool — replies appear inline in the
web UI under the comment they answer.

## Architecture

- **MCP server** (Bun + `@modelcontextprotocol/sdk`) connects to Claude Code over stdio.
- **Bun HTTP server** on `127.0.0.1:8788` serves the review UI, handles `/comment` POSTs, and broadcasts to WS clients.
- **Capabilities declared:** `experimental: { 'claude/channel': {} }` + `tools: {}` for the `reply` tool.
- **Notification method:** `notifications/claude/channel` with `content` + `meta: { comment_id, turn_idx, lines, ts }`.
- **Meta keys** are identifiers only (letters/digits/underscore) per the channels spec — values can contain anything.

## Best practices applied

| Concern | How |
|---|---|
| Sender gating | Binds to `127.0.0.1` only. No external surface. |
| Capability declaration | `experimental: { 'claude/channel': {} }` + `tools: {}` |
| Meta key naming | `comment_id`, `turn_idx`, `lines`, `ts` — all `[a-z_]` |
| Long-lived WS | `idleTimeout: 0` on `Bun.serve` |
| One-way vs two-way | Two-way: declares `reply` tool, instructions tell Claude to use it |
| Instructions | Tells Claude exact tag format + which meta key to pass to `reply` |

Permission-relay capability (`claude/channel/permission`) is **not** declared —
review-canvas does not authenticate the sender beyond localhost binding, so
allowing it to approve tool use would be unsafe.

## Setup

1. Add the local marketplace (from project root):
   ```
   /plugin marketplace add /Users/emmahyde/projects/memesis/tools/channels
   ```

2. Install the plugin:
   ```
   /plugin install review-canvas@memesis-channels
   ```

3. Point the plugin at a transcript before launch (read once at startup):
   ```bash
   export REVIEW_CANVAS_TRANSCRIPT=/Users/emmahyde/.claude/projects/<project>/<session>.jsonl
   ```

4. Exit Claude Code, then relaunch with the dev flag:
   ```bash
   claude --dangerously-load-development-channels plugin:review-canvas@memesis-channels
   ```

   Note: per the channels reference, when combined with `--channels`, the dev
   bypass is per-entry — pass the plugin under `--dangerously-load-development-channels`,
   not `--channels`, for any local-dev plugin.

5. Open the UI:
   ```
   http://localhost:8788
   ```

   Click a line to start a selection. Shift-click another line to extend.
   Type a comment, Cmd+Enter to send. The comment streams into the session
   and Claude's reply appears under it.

## Optional env vars

| Var | Default | Description |
|---|---|---|
| `REVIEW_CANVAS_PORT` | `8788` | UI/HTTP/WS port |
| `REVIEW_CANVAS_TRANSCRIPT` | `''` | Path to JSONL transcript to render |

## Troubleshooting

- **`/mcp` shows "Failed to connect"** — check `~/.claude/debug/<session-id>.txt` for stderr.
- **Port already bound** — `lsof -i :8788`, kill stale process.
- **No comments arriving** — confirm Claude Code launched with the dev flag, not just `--channels`.
- **Empty transcript** — verify `REVIEW_CANVAS_TRANSCRIPT` is set in the environment Claude Code inherits, not just your shell after launch.
