[Skip to Content](https://lightpanda.io/docs/cloud-offer/tools/cdp#nextra-skip-nav)
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
- [Usage](https://lightpanda.io/docs/cloud-offer/tools/cdp#usage)
- [Options](https://lightpanda.io/docs/cloud-offer/tools/cdp#options)
- [Browser](https://lightpanda.io/docs/cloud-offer/tools/cdp#browser)
- [Proxies](https://lightpanda.io/docs/cloud-offer/tools/cdp#proxies)
- [Connection examples](https://lightpanda.io/docs/cloud-offer/tools/cdp#connection-examples)
- [Playwright](https://lightpanda.io/docs/cloud-offer/tools/cdp#playwright)
- [Puppeteer](https://lightpanda.io/docs/cloud-offer/tools/cdp#puppeteer)
- [Chromedp](https://lightpanda.io/docs/cloud-offer/tools/cdp#chromedp)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CCDP%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/cloud-offer/tools/cdp.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top
[Cloud offer](https://lightpanda.io/docs/cloud-offer/getting-started)
ToolsCDP

# Chrome Devtool Protocol

Use the [Chrome Devtool Protocol ](https://chromedevtools.github.io/devtools-protocol/) \(CDP\) to connect to browsers. Most of existing tools to control a browser like Puppeteer, Playwright or chromedp are compatible with CDP.

## Usage[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/cdp#usage)

Depending on your location, you can connect to the CDP using the url `wss://euwest.cloud.lightpanda.io/ws` or `wss//uswest.cloud.lightpanda.io/ws`.

You have to add your token as query string parameter: `token=YOUR\_TOKEN`.

```
// Server in west europe
wss://euwest.cloud.lightpanda.io/ws?token=TOKEN
```

```
// Server in west US
wss://uswest.cloud.lightpanda.io/ws?token=TOKEN
```

### Options[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/cdp#options)

The CDP url takes options to configure the browser as query string parameters.

#### Browser[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/cdp#browser)

By default, the CDP serves [Lightpanda browsers ](https://github.com/lightpanda-io/browser). But you can select Google Chrome browser using `browser=chrome` parameter in the url. `browser=lightpanda` forces the usage of Lightpanda browser.

```
wss://euwest.cloud.lightpanda.io/ws?browser=chrome&token=TOKEN
```

#### Proxies[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/cdp#proxies)

**fast\_dc**

You can configure proxies for your browser with `proxy` query string parameter. By default, the proxy used is `fast\_dc`, a single shared datacenter IP.

**datacenter**

Set `datacenter` proxy to use a pool of shared datacenter IPs. The IPs rotate automatically.

`datacenter` proxy accepts an optional `country` query string parameter, a two letter country code.

Example using a german IP with a lightpanda browser.

```
wss://euwest.cloud.lightpanda.io/ws?proxy=datacenter&country=de&token=TOKEN
```

Please [contact us](mailto:hello@lightpanda.io) to get access to additional proxies for your specificc use case or to configure your own proxy with Lightpanda Cloud offer.

The service

## Connection examples[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/cdp#connection-examples)

You can find more script examples in the [demo ](https://github.com/lightpanda-io/demo/) repository.

### Playwright[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/cdp#playwright)

Use Lightpanda CDP with [Playwright ](https://playwright.dev/).

```
import playwright from "playwright-core";
 
const browser = await playwright.chromium.connectOverCDP(
  "wss://euwest.cloud.lightpanda.io/ws?token=TOKEN",
);
const context = await browser.newContext();
const page = await context.newPage();
 
//...
 
await page.close();
await context.close();
await browser.close();
```

More examples in [demo/playwright ](https://github.com/lightpanda-io/demo/tree/main/playwright).

### Puppeteer[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/cdp#puppeteer)

Use Lightpanda CDP with [Puppeteer ](https://pptr.dev/).

```
import puppeteer from 'puppeteer-core';
 
const browser = await puppeteer.connect({
    browserWSEndpoint: "wss://euwest.cloud.lightpanda.io/ws?token=TOKEN",
});
const context = await browser.createBrowserContext();
const page = await context.newPage();
 
// ...
 
await page.close();
await context.close();
await browser.disconnect();
```

More examples in [demo/puppeteer ](https://github.com/lightpanda-io/demo/tree/main/puppeteer).

### Chromedp[Permalink for this section](https://lightpanda.io/docs/cloud-offer/tools/cdp#chromedp)

Use Lightpanda CDP with [Chromedp ](https://github.com/chromedp/chromedp).

```
package main
 
import (
	"context"
	"log"
 
	"github.com/chromedp/chromedp"
)
 
func main() {
	ctx, cancel := chromedp.NewRemoteAllocator(context.Background(),
		"wss://euwest.cloud.lightpanda.io/ws?token=TOKEN", chromedp.NoModifyURL,
	)
	defer cancel()
 
	ctx, cancel = chromedp.NewContext(ctx)
	defer cancel()
 
	var title string
	if err := chromedp.Run(ctx,
		chromedp.Navigate("https://lightpanda.io"),
		chromedp.Title(&title),
	); err != nil {
		log.Fatalf("Failed getting title of lightpanda.io: %v", err)
	}
 
	log.Println("Got title of:", title)
}
```

More examples in [demo/chromedp ](https://github.com/lightpanda-io/demo/tree/main/chromedp).
[Getting started](https://lightpanda.io/docs/cloud-offer/getting-started)
[MCP](https://lightpanda.io/docs/cloud-offer/tools/mcp)
---

Built with [Nextra](https://nextra.site)
