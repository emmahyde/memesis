[Skip to Content](https://lightpanda.io/docs/open-source/usage#nextra-skip-nav)
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
- [Fetch a webpage](https://lightpanda.io/docs/open-source/usage#fetch-a-webpage)
- [Options](https://lightpanda.io/docs/open-source/usage#options)
- [`fetch` command options](https://lightpanda.io/docs/open-source/usage#fetch-command-options)
- [CDP server](https://lightpanda.io/docs/open-source/usage#cdp-server)
- [`serve` command options](https://lightpanda.io/docs/open-source/usage#serve-command-options)
- [Connect with Puppeteer](https://lightpanda.io/docs/open-source/usage#connect-with-puppeteer)
- [Connect with Playwright](https://lightpanda.io/docs/open-source/usage#connect-with-playwright)
- [Connect with Chromedp](https://lightpanda.io/docs/open-source/usage#connect-with-chromedp)
- [MCP server](https://lightpanda.io/docs/open-source/usage#mcp-server)
- [Tools](https://lightpanda.io/docs/open-source/usage#tools)
- [Options](https://lightpanda.io/docs/open-source/usage#options-1)
- [Claude Desktop / Cursor / Windsurf](https://lightpanda.io/docs/open-source/usage#claude-desktop--cursor--windsurf)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CUsage%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/open-source/usage.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top
[Open source edition](https://lightpanda.io/docs/open-source/installation)
Usage

# Usage

Use `./lightpanda help` for all options.

## Fetch a webpage[Permalink for this section](https://lightpanda.io/docs/open-source/usage#fetch-a-webpage)

```
./lightpanda fetch --obey-robots --dump html https://demo-browser.lightpanda.io/campfire-commerce/
```

```
INFO  http : navigate . . . . . . . . . . . . . . . . . . . . [+0ms]
      url = https://demo-browser.lightpanda.io/campfire-commerce/
      method = GET
      reason = address_bar
      body = false
 
INFO  browser : executing script . . . . . . . . . . . . . .  [+196ms]
      src = https://demo-browser.lightpanda.io/campfire-commerce/script.js
      kind = javascript
      cacheable = true
 
INFO  http : request complete . . . . . . . . . . . . . . . . [+223ms]
      source = xhr
      url = https://demo-browser.lightpanda.io/campfire-commerce/json/product.json
      status = 200
 
INFO  http : request complete . . . . . . . . . . . . . . . . [+234ms]
      source = xhr
      url = https://demo-browser.lightpanda.io/campfire-commerce/json/reviews.json
      status = 200
<!DOCTYPE html>
```

### Options[Permalink for this section](https://lightpanda.io/docs/open-source/usage#options)

### `fetch` command options[Permalink for this section](https://lightpanda.io/docs/open-source/usage#fetch-command-options)

```
--dump          Dumps document to stdout.
                Argument must be 'html', 'markdown', 'semantic_tree', or 'semantic_tree_text'.
                Defaults to no dump.
 
--strip-mode    Comma separated list of tag groups to remove from dump
                the dump. e.g. --strip-mode js,css
                  - "js" script and link[as=script, rel=preload]
                  - "ui" includes img, picture, video, css and svg
                  - "css" includes style and link[rel=stylesheet]
                  - "full" includes js, ui and css
 
--with-base     Add a <base> tag in dump. Defaults to false.
 
--with-frames   Includes the contents of iframes. Defaults to false.
 
--wait-ms       Wait time in milliseconds.
                Defaults to 5000.
 
--wait-until    Wait until the specified event.
                Supported events: load, domcontentloaded, networkidle, done.
                Defaults to 'done'.
 
--insecure-disable-tls-host-verification
                Disables host verification on all HTTP requests. This is an
                advanced option which should only be set if you understand
                and accept the risk of disabling host verification.
 
--obey-robots
                Fetches and obeys the robots.txt (if available) of the web pages
                we make requests towards.
                Defaults to false.
 
--http-proxy    The HTTP proxy to use for all HTTP requests.
                A username:password can be included for basic authentication.
                Defaults to none.
 
--proxy-bearer-token
                The <token> to send for bearer authentication with the proxy
                Proxy-Authorization: Bearer <token>
 
--http-max-concurrent
                The maximum number of concurrent HTTP requests.
                Defaults to 10.
 
--http-max-host-open
                The maximum number of open connection to a given host:port.
                Defaults to 4.
 
--http-connect-timeout
                The time, in milliseconds, for establishing an HTTP connection
                before timing out. 0 means it never times out.
                Defaults to 0.
 
--http-timeout
                The maximum time, in milliseconds, the transfer is allowed
                to complete. 0 means it never times out.
                Defaults to 10000.
 
--http-max-response-size
                Limits the acceptable response size for any request
                (e.g. XHR, fetch, script loading, ...).
                Defaults to no limit.
 
--log-level     The log level: debug, info, warn, error or fatal.
                Defaults towarn.
 
--log-format    The log format: pretty or logfmt.
                Defaults to logfmt.
 
--log-filter-scopes
                Filter out too verbose logs per scope:
                http, unknown_prop, event, ...
 
--user-agent-suffix
                Suffix to append to the Lightpanda/X.Y User-Agent
 
--web-bot-auth-key-file
                Path to the Ed25519 private key PEM file.
 
--web-bot-auth-keyid
                The JWK thumbprint of your public key.
 
--web-bot-auth-domain
                Your domain e.g. yourdomain.com
```

See also [how to configure proxy](https://lightpanda.io/docs/open-source/guides/configure-a-proxy).

## CDP server[Permalink for this section](https://lightpanda.io/docs/open-source/usage#cdp-server)

To control Lightpanda with [Chrome Devtool Protocol ](https://chromedevtools.github.io/devtools-protocol/) \(CDP\) clients like [Playwright ](https://playwright.dev/) or [Puppeteer ](https://pptr.dev/), you need to start the browser as a CDP server.

```
./lightpanda serve --obey-robots --host 127.0.0.1 --port 9222
```

```
INFO  app : server running . . . . . . . . . . . . . . . . .  [+0ms]
      address = 127.0.0.1:9222
```

### `serve` command options[Permalink for this section](https://lightpanda.io/docs/open-source/usage#serve-command-options)

```
--host          Host of the CDP server
                Defaults to "127.0.0.1"
 
--port          Port of the CDP server
                Defaults to 9222
 
--advertise-host
                The host to advertise, e.g. in the /json/version response.
                Useful, for example, when --host is 0.0.0.0.
                Defaults to --host value
 
--cdp-max-connections
                Maximum number of simultaneous CDP connections.
                Defaults to 16.
 
--cdp-max-pending-connections
                Maximum pending connections in the accept queue.
                Defaults to 128.
 
--insecure-disable-tls-host-verification
                Disables host verification on all HTTP requests. This is an
                advanced option which should only be set if you understand
                and accept the risk of disabling host verification.
 
--obey-robots
                Fetches and obeys the robots.txt (if available) of the web pages
                we make requests towards.
                Defaults to false.
 
--http-proxy    The HTTP proxy to use for all HTTP requests.
                A username:password can be included for basic authentication.
                Defaults to none.
 
--proxy-bearer-token
                The <token> to send for bearer authentication with the proxy
                Proxy-Authorization: Bearer <token>
 
--http-max-concurrent
                The maximum number of concurrent HTTP requests.
                Defaults to 10.
 
--http-max-host-open
                The maximum number of open connection to a given host:port.
                Defaults to 4.
 
--http-connect-timeout
                The time, in milliseconds, for establishing an HTTP connection
                before timing out. 0 means it never times out.
                Defaults to 0.
 
--http-timeout
                The maximum time, in milliseconds, the transfer is allowed
                to complete. 0 means it never times out.
                Defaults to 10000.
 
--http-max-response-size
                Limits the acceptable response size for any request
                (e.g. XHR, fetch, script loading, ...).
                Defaults to no limit.
 
--log-level     The log level: debug, info, warn, error or fatal.
                Defaults towarn.
 
--log-format    The log format: pretty or logfmt.
                Defaults to logfmt.
 
--log-filter-scopes
                Filter out too verbose logs per scope:
                http, unknown_prop, event, ...
 
--user-agent-suffix
                Suffix to append to the Lightpanda/X.Y User-Agent
 
--web-bot-auth-key-file
                Path to the Ed25519 private key PEM file.
 
--web-bot-auth-keyid
                The JWK thumbprint of your public key.
 
--web-bot-auth-domain
                Your domain e.g. yourdomain.com
```

See also [how to configure proxy](https://lightpanda.io/docs/open-source/guides/configure-a-proxy).

### Connect with Puppeteer[Permalink for this section](https://lightpanda.io/docs/open-source/usage#connect-with-puppeteer)

Once the CDP server started, you can run a [Puppeteer ](https://playwright.dev/) script by configuring the `browserWSEndpoint`.

```
'use strict'
 
import puppeteer from 'puppeteer-core'
 
// use browserWSEndpoint to pass the Lightpanda's CDP server address.
const browser = await puppeteer.connect({
  browserWSEndpoint: "ws://127.0.0.1:9222",
})
 
// The rest of your script remains the same.
const context = await browser.createBrowserContext()
const page = await context.newPage()
 
// Dump all the links from the page.
await page.goto('https://wikipedia.com/')
 
const links = await page.evaluate(() => {
  return Array.from(document.querySelectorAll('a')).map(row => {
    return row.getAttribute('href')
  })
})
 
console.log(links)
 
await page.close()
await context.close()
await browser.disconnect()
```

### Connect with Playwright[Permalink for this section](https://lightpanda.io/docs/open-source/usage#connect-with-playwright)

Try Lightpanda with [Playwright ](https://playwright.dev/) by using `chromium.connectOverCDP` to connect.

```
import { chromium } from 'playwright-core';
 
// use connectOverCDP to pass the Lightpanda's CDP server address.
const browser = await chromium.connectOverCDP('ws://127.0.0.1:9222');
 
// The rest of your script remains the same.
const context = await browser.newContext({});
const page = await context.newPage();
 
await page.goto('https://wikipedia.com/');
 
const title = await page.locator('h1').textContent();
console.log(title);
 
await page.close();
await context.close();
await browser.close();
```

### Connect with Chromedp[Permalink for this section](https://lightpanda.io/docs/open-source/usage#connect-with-chromedp)

Use Lightpanda with [Chromedp ](https://github.com/chromedp/chromedp), a Golang client for CDP servers.

```
package main
 
import (
    "context"
    "flag"
    "log"
 
    "github.com/chromedp/chromedp"
)
 
func main() {
    ctx, cancel = chromedp.NewRemoteAllocator(ctx,
        "ws://127.0.0.1:9222", chromedp.NoModifyURL,
    )
    defer cancel()
 
    ctx, cancel := chromedp.NewContext(allocatorContext)
    defer cancel()
 
    var title string
    if err := chromedp.Run(ctx,
        chromedp.Navigate("https://wikipedia.com/"),
        chromedp.Title(&title),
    ); err != nil {
        log.Fatalf("Failed getting page's title: %v", err)
    }
 
    log.Println("Got title of:", title)
}
```

## MCP server[Permalink for this section](https://lightpanda.io/docs/open-source/usage#mcp-server)

Starts an MCP \(Model Context Protocol\) server over stdio

```
./lightpanda mcp
```

### Tools[Permalink for this section](https://lightpanda.io/docs/open-source/usage#tools)

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

### Options[Permalink for this section](https://lightpanda.io/docs/open-source/usage#options-1)

```
--insecure-disable-tls-host-verification
                Disables host verification on all HTTP requests. This is an
                advanced option which should only be set if you understand
                and accept the risk of disabling host verification.
 
--obey-robots
                Fetches and obeys the robots.txt (if available) of the web pages
                we make requests towards.
                Defaults to false.
 
--http-proxy    The HTTP proxy to use for all HTTP requests.
                A username:password can be included for basic authentication.
                Defaults to none.
 
--proxy-bearer-token
                The <token> to send for bearer authentication with the proxy
                Proxy-Authorization: Bearer <token>
 
--http-max-concurrent
                The maximum number of concurrent HTTP requests.
                Defaults to 10.
 
--http-max-host-open
                The maximum number of open connection to a given host:port.
                Defaults to 4.
 
--http-connect-timeout
                The time, in milliseconds, for establishing an HTTP connection
                before timing out. 0 means it never times out.
                Defaults to 0.
 
--http-timeout
                The maximum time, in milliseconds, the transfer is allowed
                to complete. 0 means it never times out.
                Defaults to 10000.
 
--http-max-response-size
                Limits the acceptable response size for any request
                (e.g. XHR, fetch, script loading, ...).
                Defaults to no limit.
 
--log-level     The log level: debug, info, warn, error or fatal.
                Defaults towarn.
 
--log-format    The log format: pretty or logfmt.
                Defaults to logfmt.
 
--log-filter-scopes
                Filter out too verbose logs per scope:
                http, unknown_prop, event, ...
 
--user-agent-suffix
                Suffix to append to the Lightpanda/X.Y User-Agent
 
--web-bot-auth-key-file
                Path to the Ed25519 private key PEM file.
 
--web-bot-auth-keyid
                The JWK thumbprint of your public key.
 
--web-bot-auth-domain
                Your domain e.g. yourdomain.com
```

### Claude Desktop / Cursor / Windsurf[Permalink for this section](https://lightpanda.io/docs/open-source/usage#claude-desktop--cursor--windsurf)

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
[Installation](https://lightpanda.io/docs/open-source/installation)
[Build from sources](https://lightpanda.io/docs/open-source/guides/build-from-sources)
---

Built with [Nextra](https://nextra.site)
