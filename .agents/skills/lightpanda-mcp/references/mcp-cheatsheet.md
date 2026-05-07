# Lightpanda MCP Cheatsheet

Detailed source pages:

- `docs/wiki/lightpanda/pages/open-source-mcp-server.md`
- `docs/wiki/lightpanda/pages/open-source-usage.md`
- `docs/wiki/lightpanda/pages/open-source-markdown-axtree.md`
- `docs/wiki/lightpanda/pages/cloud-tools-mcp.md`

## Native MCP Server

Start stdio MCP:

```bash
lightpanda mcp
```

Codex-style MCP config shape:

```toml
[mcp_servers.lightpanda]
command = "/opt/homebrew/bin/lightpanda"
args = ["mcp"]
```

Robots-aware variant:

```toml
[mcp_servers.lightpanda]
command = "/opt/homebrew/bin/lightpanda"
args = ["mcp", "--obey-robots"]
```

## Tools

| Tool | Use |
| --- | --- |
| `goto` | Navigate and load a page into memory |
| `markdown` | Extract current or inline `url` page as Markdown |
| `links` | Extract links from current or inline `url` page |
| `evaluate` | Run JavaScript in page context |
| `semantic_tree` | Get simplified semantic DOM for reasoning |
| `interactiveElements` | List clickable/fillable elements |
| `structuredData` | Extract JSON-LD, OpenGraph, and similar data |
| `detectForms` | Extract form fields, types, and required state |
| `click` | Click an interactive element |
| `fill` | Fill an input, textarea, or select |
| `scroll` | Scroll page or element |
| `waitForSelector` | Wait for a CSS selector and return backend node ID |

## Resources

After a page is loaded:

- `mcp://page/html`
- `mcp://page/markdown`

The `markdown` tool and `mcp://page/markdown` resource expose the same page representation. Use tools during agent workflow; resources are host-readable background state.

## Known Behavior

`goto` can return `"Navigated successfully."` even when a URL is invalid or unreachable. Always verify with a follow-up content call such as `markdown` or `links`.

## HTTP Bridge

Lightpanda MCP is stdio-native. Use `supergateway` for streamable HTTP:

```bash
npx -y supergateway \
  --stdio "lightpanda mcp" \
  --outputTransport streamableHttp \
  --stateful --sessionTimeout 180000 \
  --port 8000
```

For stateless calls, pass the URL inline to the `markdown` tool.

## CLI Extraction

Quick Markdown:

```bash
lightpanda fetch --dump markdown --wait-until networkidle "https://example.com"
```

Semantic tree text:

```bash
lightpanda fetch --dump semantic_tree_text --wait-until networkidle "https://example.com"
```

Rendered HTML with scripts and CSS stripped:

```bash
lightpanda fetch --dump html --strip-mode js,css "https://example.com"
```

Common useful options:

- `--obey-robots`
- `--with-frames`
- `--wait-until load|domcontentloaded|networkidle|done`
- `--wait-ms <milliseconds>`
- `--http-proxy <url>`
- `--log-level info|debug|warn|error`
