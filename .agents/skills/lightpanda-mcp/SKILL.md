---
name: lightpanda-mcp
description: Use when scraping or extracting documentation with Lightpanda, configuring Lightpanda's native MCP server, deciding between Lightpanda fetch, MCP, and CDP, or searching the local Lightpanda docs wiki in this project.
---

# Lightpanda MCP

Use Lightpanda for fast, non-visual web extraction when JavaScript execution matters and a full graphical browser is unnecessary.

## Local Wiki First

Before answering detailed Lightpanda questions, search the local docs wiki:

```bash
rg -n "mcp|markdown|semantic_tree|fetch|serve|obey-robots|supergateway" docs/wiki/lightpanda
```

Read [references/mcp-cheatsheet.md](references/mcp-cheatsheet.md) for the compact MCP and scraping workflow. Read the specific wiki page under `docs/wiki/lightpanda/pages/` when exact options or examples matter.

## Choose the Interface

| Need | Use |
| --- | --- |
| One page to Markdown, HTML, or semantic tree | `lightpanda fetch --dump ... <url>` |
| Agent workflow with page state, clicks, fills, forms, and extraction | `lightpanda mcp` |
| Playwright/Puppeteer automation through WebSocket | `lightpanda serve` with CDP |
| HTTP access to MCP tools | `supergateway` bridge around `lightpanda mcp` |

## Extraction Defaults

Prefer Markdown for LLM context:

```bash
lightpanda fetch --dump markdown --wait-until networkidle "https://example.com"
```

Use semantic tree when the page structure or interactive targets matter:

```bash
lightpanda fetch --dump semantic_tree_text --wait-until networkidle "https://example.com"
```

Respect robots.txt when doing repeated or broad extraction:

```bash
lightpanda fetch --obey-robots --dump markdown "https://example.com"
```

## MCP Rules

- Start the native server with `lightpanda mcp`.
- Prefer the `markdown` tool with an inline `url` for stateless single-call extraction.
- Do not trust `goto` alone; it can return success for unreachable URLs. Verify with `markdown`, `links`, or another content call.
- Use `semantic_tree`, `interactiveElements`, and `detectForms` before clicking or filling.
- Logs go to stderr. Increase verbosity with `--log-level info` or `--log-level debug`.

## Local Validation

After refreshing the wiki or editing this skill, run:

```bash
rg -n "Use Native MCP server|markdown|semantic_tree|goto always returns success" docs/wiki/lightpanda .agents/skills/lightpanda-mcp
```
