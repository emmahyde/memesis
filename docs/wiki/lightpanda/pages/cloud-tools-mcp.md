[Skip to Content](https://lightpanda.io/docs/cloud-offer/tools/mcp#nextra-skip-nav)
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
- [Usage](https://lightpanda.io/docs/cloud-offer/tools/mcp#usage)
- [Authentication](https://lightpanda.io/docs/cloud-offer/tools/mcp#authentication)
- [Tools](https://lightpanda.io/docs/cloud-offer/tools/mcp#tools)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CMCP%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/cloud-offer/tools/mcp.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top
[Cloud offer](https://lightpanda.io/docs/cloud-offer/getting-started)
[Tools](https://lightpanda.io/docs/cloud-offer/tools/cdp)
MCP

# Model Context Protocol

Use the [Model Context Protocol ](https://modelcontextprotocol.io) \(MCP\) to easily control Lightpanda browser with your AI applications.

## Usage[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/mcp#usage)

The Lightpanda MCP service supports only [SSE ](https://modelcontextprotocol.io/specification/2024-11-05/basic/transports#http-with-sse) transport.

Depending on your location, you can connect to the MCP using the url `wss://euwest.cloud.lightpanda.io/mcp/sse` or `wss//uswest.cloud.lightpanda.io/mcp/sse`.

### Authentication[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/mcp#authentication)

An authentication is required, you can either pass your token with the `token` query string parameter in the url, or use the `Authorization: Bearer` HTTP header.

Example with the query string.

```
https://euwest.cloud.lightpanda.io/mcp/sse?token=TOKEN
```

Example with the Bearer HTTP header.

```
https://euwest.cloud.lightpanda.io/mcp/sse
Authorization: Bearer TOKEN
```

## Tools[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/mcp#tools)
- `search` Search a term on web search engine and get the search results. 
- `goto` Navigate to a specified URL and load the page inmemory so it can be reused later for info extraction. 
- `markdown` Get the page in memory content in markdown format.Run a goto before getting markdown. 
- `links` Extract all links from the page in memory.Run a goto before getting links. 

For more advanced use cases, you can use [CDP](https://lightpanda.io/docs/cloud-offer/tools/cdp) connection with [Playwright MCP ](https://github.com/microsoft/playwright-mcp).
[CDP](https://lightpanda.io/docs/cloud-offer/tools/cdp)
---

Built with [Nextra](https://nextra.site)
