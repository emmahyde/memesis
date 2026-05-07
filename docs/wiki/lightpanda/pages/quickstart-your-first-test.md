[Skip to Content](https://lightpanda.io/docs/quickstart/your-first-test#nextra-skip-nav)
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
- [Connect CDP Client to Lightpanda](https://lightpanda.io/docs/quickstart/your-first-test#connect-cdp-client-to-lightpanda)
- [Extract all reference links from Wikipedia](https://lightpanda.io/docs/quickstart/your-first-test#extract-all-reference-links-from-wikipedia)
- [Execute the link extraction](https://lightpanda.io/docs/quickstart/your-first-test#execute-the-link-extraction)
- [Step 3: Extract data](https://lightpanda.io/docs/quickstart/your-first-test#step-3-extract-data)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CYour%20first%20test%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/quickstart/your-first-test.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top

# 2. Your first test

Lightpanda is a headless browser built from scratch. Unlike Headless Chrome, it has no UI or graphical rendering for humans, which allows it to start instantly and execute pages up to 10x faster.

Unlike [curl ](https://curl.se/), which only fetches raw HTML, Lightpanda can execute JavaScript and run query selectors directly in the browser.

It’s ideal for crawling, testing, and running AI agents that need to interact with dynamic web pages, and it’s fully compatible with libraries like [Puppeteer ](https://pptr.dev/) and [Playwright ](https://playwright.dev/).

In this example, you’ll connect cd CDP client, [Puppeteer ](https://pptr.dev/) or [Playwright ](https://playwright.dev/) to Lightpanda and extract all reference links from a [Wikipedia page ](https://www.wikipedia.org/).

## Connect CDP Client to Lightpanda[Permalink for this section](https://lightpanda.io/docs/quickstart/your-first-test#connect-cdp-client-to-lightpanda)

Install the [`puppeteer\-core`](https://www.npmjs.com/package/puppeteer-core) *or* [`playwright\-core`](https://www.npmjs.com/package/playwright-core) npm package.

Unlike `puppeteer` and `playwright` npm packages, `puppeteer\-core` and `playwright\-core` don’t download a Chromium browser.
puppeteerplaywright

### puppeteer

```
npm install -save puppeteer-core
```

### playwright

```
npm install -save playwright-core
```

Edit your `index.js` to connect to Lightpanda:
puppeteerplaywright

### puppeteer

```
'use strict'
 
import { lightpanda } from '@lightpanda/browser';
import puppeteer from 'puppeteer-core';
 
const lpdopts = {
  host: '127.0.0.1',
  port: 9222,
};
 
const puppeteeropts = {
  browserWSEndpoint: 'ws://' + lpdopts.host + ':' + lpdopts.port,
};
 
(async () => {
  // Start Lightpanda browser in a separate process.
  const proc = await lightpanda.serve(lpdopts);
 
  // Connect Puppeteer to the browser.
  const browser = await puppeteer.connect(puppeteeropts);
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
 
  // Do your magic ✨
  console.log("CDP connection is working");
 
  // Disconnect Puppeteer.
  await page.close();
  await context.close();
  await browser.disconnect();
 
  // Stop Lightpanda browser process.
  proc.stdout.destroy();
  proc.stderr.destroy();
  proc.kill();
})();
```

### playwright

```
'use strict'
 
import { lightpanda } from '@lightpanda/browser';
import { chromium } from 'playwright-core';
 
const lpdopts = {
  host: '127.0.0.1',
  port: 9222,
};
 
const playwrightopts = {
  endpointURL: 'ws://' + lpdopts.host + ':' + lpdopts.port,
};
 
(async () => {
  // Start Lightpanda browser in a separate process.
  const proc = await lightpanda.serve(lpdopts);
 
  // Connect Playwright's chromium driver to the browser.
  const browser = await chromium.connectOverCDP(playwrightopts);
  const context = await browser.newContext({});
  const page = await context.newPage();
 
  // Do your magic ✨
  console.log("CDP connection is working");
 
  // Disconnect Puppeteer.
  await page.close();
  await context.close();
  await browser.close();
 
  // Stop Lightpanda browser process.
  proc.stdout.destroy();
  proc.stderr.destroy();
  proc.kill();
})();
```

Run the script to test the connection between Puppeteer or Playwright and Lightpanda:

```
node index.js
```

```
$ node index.js
🐼 Running Lightpanda's CDP server... { pid: 31371 }
CDP connection is working
```

## Extract all reference links from Wikipedia[Permalink for this section](https://lightpanda.io/docs/quickstart/your-first-test#extract-all-reference-links-from-wikipedia)

Update `index.js` using `page.goto` to navigate to a Wikipedia page and extract all the reference links:
puppeteerplaywright

### puppeteer

```
  // Go to Wikipedia page.
  await page.goto("https://en.wikipedia.org/wiki/Web_browser");
```

### playwright

```
  // Go to Wikipedia page.
  await page.goto("https://en.wikipedia.org/wiki/Web_browser");
```

Execute a query selector on the browser to extract the links:
puppeteerplaywright

### puppeteer

```
  // Extract all links from the references list of the page.
  const reflist = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('.references a.external')).map(row => {
      return row.getAttribute('href');
    });
  });
```

### playwright

```
  // Extract all links from the references list of the page.
  const reflist = await page.locator('.references a.external').evaluateAll(links =>
    links.map(link => link.getAttribute('href'))
  );
```

Here’s the full `index.js` file:
puppeteerplaywright

### puppeteer

```
'use strict'
 
import { lightpanda } from '@lightpanda/browser';
import puppeteer from 'puppeteer-core';
 
const lpdopts = {
  host: '127.0.0.1',
  port: 9222,
};
 
const puppeteeropts = {
  browserWSEndpoint: 'ws://' + lpdopts.host + ':' + lpdopts.port,
};
 
(async () => {
  // Start Lightpanda browser in a separate process.
  const proc = await lightpanda.serve(lpdopts);
 
  // Connect Puppeteer to the browser.
  const browser = await puppeteer.connect(puppeteeropts);
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
 
  // Go to Wikipedia page.
  await page.goto("https://en.wikipedia.org/wiki/Web_browser");
 
  // Extract all links from the references list of the page.
  const reflist = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('.references a.external')).map(row => {
      return row.getAttribute('href');
    });
  });
 
  // Display the result.
  console.log("all reference links", reflist);
 
  // Disconnect Puppeteer.
  await page.close();
  await context.close();
  await browser.disconnect();
 
  // Stop Lightpanda browser process.
  proc.stdout.destroy();
  proc.stderr.destroy();
  proc.kill();
})();
```

### playwright

```
'use strict'
 
import { lightpanda } from '@lightpanda/browser';
import { chromium } from 'playwright-core';
 
const lpdopts = {
  host: '127.0.0.1',
  port: 9222,
};
 
const playwrightopts = {
  endpointURL: 'ws://' + lpdopts.host + ':' + lpdopts.port,
};
 
(async () => {
  // Start Lightpanda browser in a separate process.
  const proc = await lightpanda.serve(lpdopts);
 
  // Connect using Playwright's chromium driver to the browser.
  const browser = await chromium.connectOverCDP(playwrightopts);
  const context = await browser.newContext({});
  const page = await context.newPage();
 
  // Go to Wikipedia page.
  await page.goto("https://en.wikipedia.org/wiki/Web_browser");
 
  // Extract all links from the references list of the page.
  const reflist = await page.locator('.references a.external').evaluateAll(links =>
    links.map(link => link.getAttribute('href'))
  );
 
  // Display the result.
  console.log("all reference links", reflist);
 
  // Disconnect Playwright.
  await page.close();
  await context.close();
  await browser.close();
 
  // Stop Lightpanda browser process.
  proc.stdout.destroy();
  proc.stderr.destroy();
  proc.kill();
})();
```

## Execute the link extraction[Permalink for this section](https://lightpanda.io/docs/quickstart/your-first-test#execute-the-link-extraction)

Execute index.js to see the links directly in your console:

```
node index.js
```

```
$ node index.js
🐼 Running Lightpanda's CDP server... { pid: 34389 }
all reference links [
  'https://gs.statcounter.com/browser-market-share',
  'https://radar.cloudflare.com/reports/browser-market-share-2024-q1',
  'https://web.archive.org/web/20240523140912/https://www.internetworldstats.com/stats.htm',
  'https://www.internetworldstats.com/stats.htm',
  'https://www.reference.com/humanities-culture/purpose-browser-e61874e41999ede',
```

### Step 3: [Extract data](https://lightpanda.io/docs/quickstart/build-your-first-extraction-script)[Permalink for this section](https://lightpanda.io/docs/quickstart/your-first-test#step-3-extract-data)
[1. Installation and setup](https://lightpanda.io/docs/quickstart/installation-and-setup)
[3. Extract data](https://lightpanda.io/docs/quickstart/build-your-first-extraction-script)
---

Built with [Nextra](https://nextra.site)
