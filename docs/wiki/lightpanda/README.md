# Lightpanda Docs Wiki

Local Markdown capture of Lightpanda documentation for fast repo search and skill reference.

Source root: https://lightpanda.io/docs

Captured pages live in [pages](pages). Search with:

```bash
rg -n "mcp|markdown|semantic_tree|obey-robots|supergateway" docs/wiki/lightpanda
```

## Page Index

| Topic | Local file | Source |
| --- | --- | --- |
| Introduction | [pages/docs.md](pages/docs.md) | https://lightpanda.io/docs |
| Quickstart: install | [pages/quickstart-installation-and-setup.md](pages/quickstart-installation-and-setup.md) | https://lightpanda.io/docs/quickstart/installation-and-setup |
| Quickstart: first test | [pages/quickstart-your-first-test.md](pages/quickstart-your-first-test.md) | https://lightpanda.io/docs/quickstart/your-first-test |
| Quickstart: extraction script | [pages/quickstart-build-your-first-extraction-script.md](pages/quickstart-build-your-first-extraction-script.md) | https://lightpanda.io/docs/quickstart/build-your-first-extraction-script |
| Quickstart: production | [pages/quickstart-go-to-production.md](pages/quickstart-go-to-production.md) | https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud |
| Open source: installation | [pages/open-source-installation.md](pages/open-source-installation.md) | https://lightpanda.io/docs/open-source/installation |
| Open source: usage | [pages/open-source-usage.md](pages/open-source-usage.md) | https://lightpanda.io/docs/open-source/usage |
| Open source: build from sources | [pages/open-source-build-from-sources.md](pages/open-source-build-from-sources.md) | https://lightpanda.io/docs/open-source/guides/build-from-sources |
| Open source: proxy | [pages/open-source-configure-a-proxy.md](pages/open-source-configure-a-proxy.md) | https://lightpanda.io/docs/open-source/guides/configure-a-proxy |
| Open source: Markdown and AXTree | [pages/open-source-markdown-axtree.md](pages/open-source-markdown-axtree.md) | https://lightpanda.io/docs/open-source/guides/markdown-axtree |
| Open source: native MCP server | [pages/open-source-mcp-server.md](pages/open-source-mcp-server.md) | https://lightpanda.io/docs/open-source/guides/mcp-server |
| Open source: Stagehand | [pages/open-source-use-stagehand.md](pages/open-source-use-stagehand.md) | https://lightpanda.io/docs/open-source/guides/use-stagehand |
| Open source: systems requirements | [pages/open-source-systems-requirements.md](pages/open-source-systems-requirements.md) | https://lightpanda.io/docs/open-source/systems-requirements |
| Cloud: getting started | [pages/cloud-getting-started.md](pages/cloud-getting-started.md) | https://lightpanda.io/docs/cloud-offer/getting-started |
| Cloud: CDP | [pages/cloud-tools-cdp.md](pages/cloud-tools-cdp.md) | https://lightpanda.io/docs/cloud-offer/tools/cdp |
| Cloud: MCP | [pages/cloud-tools-mcp.md](pages/cloud-tools-mcp.md) | https://lightpanda.io/docs/cloud-offer/tools/mcp |

## Capture Notes

- Most pages were captured with `/opt/homebrew/bin/lightpanda fetch --dump markdown --wait-until networkidle`.
- `open-source-markdown-axtree.md` and `quickstart-build-your-first-extraction-script.md` were captured from the official GitHub MDX source because the rendered page returned a client-side application error in Lightpanda.
- Tavily CLI was installed during setup, but no Tavily API key was present, so the wiki capture used local Lightpanda extraction instead.
