[Skip to Content](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#nextra-skip-nav)
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
- [Configure HTTP proxy](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#configure-http-proxy)
- [HTTP proxy with basic auth](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#http-proxy-with-basic-auth)
- [HTTP proxy with bearer auth](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#http-proxy-with-bearer-auth)
- [Configure a proxy from your Puppeteer/Playwright script](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#configure-a-proxy-from-your-puppeteerplaywright-script)
- [Puppeteer](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#puppeteer)
- [Playwright](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#playwright)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CConfigure%20a%20proxy%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/open-source/guides/configure-a-proxy.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top
[Open source edition](https://lightpanda.io/docs/open-source/installation)
[Guides](https://lightpanda.io/docs/open-source/guides/build-from-sources)
Configure a proxy

# Configure a proxy

Lightpanda supports HTTP and HTTPS proxies with basic or bearer authentication. You can configure the proxy when starting the browser.

## Configure HTTP proxy[Permalink for this section](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#configure-http-proxy)

Use the CLI option `\-\-http\-proxy` when starting Lightpanda to configure the proxy. Ensure your proxy address starts with `http://` or `https://`.

Use a local proxy with the `fetch` command:

```
./lightpanda fetch --http-proxy http://127.0.0.1:3000 https://lightpanda.io
```

Or configure the proxy with `serve` for the CDP server. All outgoing requests will use the proxy.

```
./lightpanda serve --http-proxy http://127.0.0.1:3000
```

### HTTP proxy with basic auth[Permalink for this section](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#http-proxy-with-basic-auth)

You can configure [basic auth ](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Authentication#basic) for the proxy using the `username:password@` format in the proxy address. It works for both `fetch` and `serve` commands.

```
./lightpanda fetch --http-proxy 'http://me:my-password@127.0.0.1:3000' https://lightpanda.io
```

### HTTP proxy with bearer auth[Permalink for this section](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#http-proxy-with-bearer-auth)

Lightpanda supports [bearer auth ](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Authentication#bearer) to authenticate with the `\-\-proxy\-bearer\-token`. It works for both `fetch` and `serve` commands.

This option will add a `Proxy\-Authorization` header all the outgoing requests.

```
./lightpanda fetch --http-proxy 'http://127.0.0.1:3000' --proxy-bearer-token 'MY-TOKEN' https://lightpanda.io
```

## Configure a proxy from your Puppeteer/Playwright script[Permalink for this section](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#configure-a-proxy-from-your-puppeteerplaywright-script)

Instead of configuring your proxy auth on Lightpanda’s start, you can pass your username and password in flight from your script using request interceptions.

### Puppeteer[Permalink for this section](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#puppeteer)

With Puppeteer, you have to configure the proxy address when starting Lightpanda.

```
./lightpanda fetch --http-proxy 'http://127.0.0.1:3000'
```

Then you can call `page.authenticate` function to inject your authentication from your script.

```
const page = await context.newPage();
 
// Set credentials for HTTP Basic Auth
await page.authenticate({
  username: 'my_username',
  password: 'my_password',
});
```

You can find the full example in our [demo repository ](https://github.com/lightpanda-io/demo/blob/main/puppeteer/proxy_auth.js).

### Playwright[Permalink for this section](https://lightpanda.io/docs/open-source/guides/configure-a-proxy#playwright)

With Playwright, configure the proxy when creating the browser’s context.

```
const context = await browser.newContext({
    baseURL: baseURL,
    proxy: {
      server: 'http://127.0.0.1:3000',
      username: 'my_username',
      password: 'my_password',
    },
});
 
const page = await context.newPage();
```

You can find the full example in our [demo repository ](https://github.com/lightpanda-io/demo/blob/main/playwright/proxy_auth.js).
[Build from sources](https://lightpanda.io/docs/open-source/guides/build-from-sources)
[Markdown and AXTree](https://lightpanda.io/docs/open-source/guides/markdown-axtree)
---

Built with [Nextra](https://nextra.site)
