[Skip to Content](https://lightpanda.io/docs/open-source/guides/mcp-server#nextra-skip-nav)
[](https://lightpanda.io/)
[Back to website ](https://lightpanda.io)
CTRL K
CTRL K
- [Back to website ](https://lightpanda.io)
- [Introduction](https://lightpanda.io/docs)
- Quickstart
  - [1. Installation and setup](https://lightpanda.io/docs/quickstart/installation-and-setup)
  - [2. Your first test](https://lightpanda.io/docs/quickstart/your-first-test)
  - [3. Extract data](https://lightpanda.io/docs/quickstart/build-your-first-extraction-script)
  - [4. Go to production](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud)
- Open source edition
  - [Installation](https://lightpanda.io/docs/open-source/installation)
  - [Usage](https://lightpanda.io/docs/open-source/usage)
  - Guides
    - [Build from sources](https://lightpanda.io/docs/open-source/guides/build-from-sources)
    - [Configure a proxy](https://lightpanda.io/docs/open-source/guides/configure-a-proxy)
    - [Markdown and AXTree](https://lightpanda.io/docs/open-source/guides/markdown-axtree)
    - [Native MCP server](https://lightpanda.io/docs/open-source/guides/mcp-server)
    - [Use Stagehand](https://lightpanda.io/docs/open-source/guides/use-stagehand)
  - [Systems requirements](https://lightpanda.io/docs/open-source/systems-requirements)
- Cloud offer
  - [Getting started](https://lightpanda.io/docs/cloud-offer/getting-started)
  - Tools
    - [CDP](https://lightpanda.io/docs/cloud-offer/tools/cdp)
    - [MCP](https://lightpanda.io/docs/cloud-offer/tools/mcp)
- [Introduction](https://lightpanda.io/docs)
- Quickstart
  - [1. Installation and setup](https://lightpanda.io/docs/quickstart/installation-and-setup)
  - [2. Your first test](https://lightpanda.io/docs/quickstart/your-first-test)
  - [3. Extract data](https://lightpanda.io/docs/quickstart/build-your-first-extraction-script)
  - [4. Go to production](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud)
- Open source edition
  - [Installation](https://lightpanda.io/docs/open-source/installation)
  - [Usage](https://lightpanda.io/docs/open-source/usage)
  - Guides
    - [Build from sources](https://lightpanda.io/docs/open-source/guides/build-from-sources)
    - [Configure a proxy](https://lightpanda.io/docs/open-source/guides/configure-a-proxy)
    - [Markdown and AXTree](https://lightpanda.io/docs/open-source/guides/markdown-axtree)
    - [Native MCP server](https://lightpanda.io/docs/open-source/guides/mcp-server)
    - [Use Stagehand](https://lightpanda.io/docs/open-source/guides/use-stagehand)
  - [Systems requirements](https://lightpanda.io/docs/open-source/systems-requirements)
- Cloud offer
  - [Getting started](https://lightpanda.io/docs/cloud-offer/getting-started)
  - Tools
    - [CDP](https://lightpanda.io/docs/cloud-offer/tools/cdp)
    - [MCP](https://lightpanda.io/docs/cloud-offer/tools/mcp)

On This Page
- [Tools and resources](https://lightpanda.io/docs/open-source/guides/mcp-server#tools-and-resources)
- [`goto`](https://lightpanda.io/docs/open-source/guides/mcp-server#goto)
- [`markdown`](https://lightpanda.io/docs/open-source/guides/mcp-server#markdown)
- [`links`](https://lightpanda.io/docs/open-source/guides/mcp-server#links)
- [`evaluate`](https://lightpanda.io/docs/open-source/guides/mcp-server#evaluate)
- [Resources](https://lightpanda.io/docs/open-source/guides/mcp-server#resources)
- [Connecting an AI agent](https://lightpanda.io/docs/open-source/guides/mcp-server#connecting-an-ai-agent)
- [Claude Desktop / Cursor / Windsurf](https://lightpanda.io/docs/open-source/guides/mcp-server#claude-desktop--cursor--windsurf)
- [HTTP transport via supergateway](https://lightpanda.io/docs/open-source/guides/mcp-server#http-transport-via-supergateway)
- [Calling the HTTP endpoint](https://lightpanda.io/docs/open-source/guides/mcp-server#calling-the-http-endpoint)
- [Known behaviors](https://lightpanda.io/docs/open-source/guides/mcp-server#known-behaviors)
- [`goto` always returns success](https://lightpanda.io/docs/open-source/guides/mcp-server#goto-always-returns-success)
- [Debugging](https://lightpanda.io/docs/open-source/guides/mcp-server#debugging)
- [References](https://lightpanda.io/docs/open-source/guides/mcp-server#references)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CNative%20MCP%20server%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/open-source/guides/mcp-server.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top
[Open source edition](https://lightpanda.io/docs/open-source/installation)
[Guides](https://lightpanda.io/docs/open-source/guides/build-from-sources)
Native MCP server

# Use Native MCP server

Lightpanda v0.2.5 ships a **native Model Context Protocol \(MCP\) server** built directly into the browser binary. The MCP server shares the same process as the Zig\-based JavaScript engine with no CDP intermediary and no extra processes.

```
lightpanda mcp
```

The server communicates via **MCP JSON\-RPC 2.0 over stdio**, making it compatible with Claude Desktop, Cursor, Windsurf, and any MCP\-aware agent framework.

## Tools and resources[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#tools-and-resources)

| Name | Description |
|---|---|
| goto | Navigate to a specified URL and load the page in memory so it can be reused later for info extraction |
| markdown | Get the page content in markdown format. If a url is provided, it navigates to that url first. |
| links | Extract all links in the opened page. If a url is provided, it navigates to that url first. |
| evaluate | Evaluate JavaScript in the current page context. If a url is provided, it navigates to that url first. |
| semantic\_tree | Get the page content as a simplified semantic DOM tree for AI reasoning. If a url is provided, it navigates to that url first. |
| interactiveElements | Extract interactive elements from the opened page. If a url is provided, it navigates to that url first. |
| structuredData | Extract structured data \(like JSON\-LD, OpenGraph, etc\) from the opened page. If a url is provided, it navigates to that url first. |
| detectForms | Detect all forms on the page and return their structure including fields, types, and required status. If a url is provided, it navigates to that url first. |
| click | Click on an interactive element. Returns the current page URL and title after the click. |
| fill | Fill text into an input element. Returns the filled value and current page URL and title. |
| scroll | Scroll the page or a specific element. Returns the scroll position and current page URL and title. |
| waitForSelector | Wait for an element matching a CSS selector to appear in the page. Returns the backend node ID of the matched element. |

#### `goto`[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#goto)

Navigate to a URL and load the page into memory.

```
{"jsonrpc":"2.0","id":2,"method":"tools/call",
 "params":{"name":"goto","arguments":{"url":"https://example.com"}}}
```

**Response:** `"Navigated successfully."` \- returned even if the URL is unreachable. Always verify with a follow\-up content call \(see [Known behaviors](https://lightpanda.io/docs/open-source/guides/mcp-server#known-behaviors)\).

#### `markdown`[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#markdown)

Extract the current page’s content as clean, token\-efficient markdown.

```
{"jsonrpc":"2.0","id":3,"method":"tools/call",
 "params":{"name":"markdown","arguments":{"url":"https://example.com"}}}
```

### Response example

```
{"result":{"content":[{"type":"text","text":"\n# Example Domain\n\nThis domain is for use in illustrative examples in documents...\n\n[More information...](https://www.iana.org/domains/example)\n"}],"isError":false}}
```

>  

Using `markdown` with an inline `url` is the most efficient single\-call pattern \- it navigates and extracts in one request. Essential for HTTP transport where sessions are stateless.

#### `links`[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#links)

Extract all hyperlinks from the loaded page as a newline\-separated list of absolute URLs.

```
{"jsonrpc":"2.0","id":4,"method":"tools/call",
 "params":{"name":"links","arguments":{"url":"https://example.com"}}}
```

**Response:** One URL per line, e.g. `"https://iana.org/domains/example"`.

#### `evaluate`[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#evaluate)

Execute arbitrary JavaScript in the page context and return the result as a string.

```
{"jsonrpc":"2.0","id":5,"method":"tools/call",
 "params":{"name":"evaluate","arguments":{
   "script":"document.title",
   "url":"https://example.com"}}}
```

**Response:** `"Example Domain"`

### Resources[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#resources)

Two read\-only resources are available after a page has been loaded via `resources/read`:

| URI | MIME type | Description |
|---|---|---|
| `mcp://page/html` | `text/html` | Raw serialized HTML DOM of the loaded page |
| `mcp://page/markdown` | `text/markdown` | Markdown representation \(identical output to the `markdown` tool\) |

```
{"jsonrpc":"2.0","id":6,"method":"resources/read",
 "params":{"uri":"mcp://page/html"}}
```

```
{"jsonrpc":"2.0","id":7,"method":"resources/read",
 "params":{"uri":"mcp://page/markdown"}}
```

>  

The `markdown` tool and the `mcp://page/markdown` resource return the same content. The difference is who initiates: **tools** are called by the agent during its workflow; **resources** are read by the host application \(e.g. an IDE displaying page state in the background\).

## Connecting an AI agent[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#connecting-an-ai-agent)

### Claude Desktop / Cursor / Windsurf[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#claude-desktop--cursor--windsurf)

Add to your MCP host configuration:
- **Claude Desktop:** Settings > Developer > Edit Config 
- **Cursor:** `.cursor/mcp.json` in your project 
- **Windsurf:** Cascade MCP settings 

```
{
  "mcpServers": {
    "lightpanda": {
      "command": "/path/to/lightpanda",
      "args": ["mcp"]
    }
  }
}
```

For robots.txt compliance, use `"args": \["mcp", "\-\-obey\_robots"\]`.

>  

Replace `/path/to/lightpanda` with the actual binary path, e.g. `/usr/local/bin/lightpanda`.

### HTTP transport via supergateway[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#http-transport-via-supergateway)

Lightpanda MCP natively supports only stdio. To expose it over HTTP, use [supergateway ](https://www.npmjs.com/package/supergateway) as a bridge.

```
npx -y supergateway \
  --stdio "lightpanda mcp" \
  --outputTransport streamableHttp \
  --stateful --sessionTimeout 180000 \
  --port 8000
```

>  

By default, supergateway is **stateless**: each HTTP request spawns a fresh process. For stateful sessions, we use `\-\-stateful \-\-sessionTimeout <ms>` to the supergateway command.

With robots.txt: `\-\-stdio "lightpanda mcp \-\-obey\-robots"`

#### Calling the HTTP endpoint[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#calling-the-http-endpoint)

```
# Initialize
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
        "params":{"protocolVersion":"2024-11-05","capabilities":{},
                  "clientInfo":{"name":"curl-test","version":"1.0"}}}'
 
# Extract markdown (pass url inline - HTTP is stateless by default)
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
        "params":{"name":"markdown","arguments":{"url":"https://example.com"}}}'
```

## Known behaviors[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#known-behaviors)

### `goto` always returns success[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#goto-always-returns-success)

`goto` returns `"Navigated successfully."` even for invalid or unreachable URLs. The failure surfaces on the next content call:

```
# Navigation failed

Reason: CouldntResolveHost
```

Always check the content result after navigation, not the `goto` response itself.

### Debugging[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#debugging)

Lightpanda defaults to `\-\-log\-level warn`. Setting `info` surfaces HTTP requests, navigation events, resource loading, and robots.txt fetches. All logs go to **stderr** and never interfere with stdout.

```
lightpanda mcp --log_level info --log_format pretty
 
# Or pipe logs to a file
lightpanda mcp --log_level info 2>lightpanda.log
```

Use `\-\-log\_level debug` for the most verbose output. Keep `warn` in production.

## References[Permalink for this section](https://lightpanda.io/docs/open-source/guides/mcp-server#references)
- [LP Domain & Native MCP \- Lightpanda Blog ](https://lightpanda.io/blog/posts/lp-domain-commands-and-native-mcp) 
- [Lightpanda GitHub ](https://github.com/lightpanda-io/browser) 
- [Lightpanda releases ](https://github.com/lightpanda-io/browser/releases) 
- [MCP Specification ](https://modelcontextprotocol.io/) 
- [supergateway \(npm\) ](https://www.npmjs.com/package/supergateway) 
- [Lightpanda Cloud MCP \(SSE\) ](https://lightpanda.io/docs/cloud-offer/tools/mcp) 
[Markdown and AXTree](https://lightpanda.io/docs/open-source/guides/markdown-axtree)
[Use Stagehand](https://lightpanda.io/docs/open-source/guides/use-stagehand)
---

Built with [Nextra](https://nextra.site)
