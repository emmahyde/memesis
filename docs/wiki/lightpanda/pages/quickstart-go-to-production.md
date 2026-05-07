[Skip to Content](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud#nextra-skip-nav)
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
- [Clean up local\-only lines](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud#clean-up-local-only-lines)
- [Final version](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud#final-version)
- [Interested in on premise deployment?](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud#interested-in-on-premise-deployment)
- [Need help?](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud#need-help)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CGo%20to%20production%20with%20Lightpanda%20cloud%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/quickstart/go-to-production-with-lightpanda-cloud.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top

# 4. Go to production

Use [Lightpanda’s cloud offer ](https://lightpanda.io/#cloud-offer) to switch from a local browser to a remotely managed version.

Create a new account and an API token [here ](https://console.lightpanda.io/signup).

To connect, the script will use an environment variable named `LPD\_TOKEN`. First export the variable with your token.

```
export LPD_TOKEN="paste your token here"
```

Edit `index.js` to change the Puppeteer connection options:
puppeteerplaywright

### puppeteer

```
const puppeteeropts = {
  browserWSEndpoint: 'wss://euwest.cloud.lightpanda.io/ws?token=' + process.env.LPD_TOKEN,
};
```

### playwright

```
const playwrightopts = {
  endpointURL: 'wss://euwest.cloud.lightpanda.io/ws?token=' + process.env.LPD_TOKEN,
};
```
ℹ️

Depending on your location, you can connect using the url `wss://euwest.cloud.lightpanda.io/ws` or `wss//uswest.cloud.lightpanda.io/ws`.

## Clean up local\-only lines[Permalink for this section](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud#clean-up-local-only-lines)

You no longer need to start a local browser process because you are using the cloud version. You can remove these parts of the script to simplify it:

```
import { lightpanda } from '@lightpanda/browser';
```

```
const lpdopts = {
  host: '127.0.0.1',
  port: 9222,
};
```

```
  // Start Lightpanda browser in a separate process.
  const proc = await lightpanda.serve(lpdopts);
```

```
  // Stop Lightpanda browser process.
  proc.stdout.destroy();
  proc.stderr.destroy();
  proc.kill();
```

## Final version[Permalink for this section](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud#final-version)

Here is the final script using the cloud browser version:
puppeteerplaywright

### puppeteer

```
'use strict'
 
import puppeteer from 'puppeteer-core';
 
const puppeteeropts = {
  browserWSEndpoint: 'wss://euwest.cloud.lightpanda.io/ws?token=' + process.env.LPD_TOKEN,
};
 
(async () => {
  // Connect Puppeteer to the browser.
  const browser = await puppeteer.connect(puppeteeropts);
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
 
  // Go to hackernews home page.
  await page.goto("https://news.ycombinator.com/");
 
  // Find the search box at the bottom of the page and type the term lightpanda
  // to search.
  await page.type('input[name="q"]','lightpanda');
  // Press enter key to run the search.
  await page.keyboard.press('Enter');
 
  // Wait until the search results are loaded on the page, with a 5 seconds
  // timeout limit.
  await page.waitForFunction(() => {
      return document.querySelector('.Story_container') != null;
  }, {timeout: 5000});
 
  // Loop over search results to extract data.
  const res = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('.Story_container')).map(row => {
      return {
        // Extract the title.
        title: row.querySelector('.Story_title span').textContent,
        // Extract the URL.
        url: row.querySelector('.Story_title a').getAttribute('href'),
        // Extract the list of meta data.
        meta: Array.from(row.querySelectorAll('.Story_meta > span:not(.Story_separator, .Story_comment)')).map(row => {
          return row.textContent;
        }),
      }
    });
  });
 
  // Display the result.
  console.log(res);
 
  // Disconnect Puppeteer.
  await page.close();
  await context.close();
  await browser.disconnect();
})();
```

### playwright

```
'use strict'
 
import { chromium } from 'playwright-core';
 
const playwrightopts = {
  endpointURL: 'wss://euwest.cloud.lightpanda.io/ws?token=' + process.env.LPD_TOKEN,
};
 
(async () => {
  // Connect using Playwright's chromium driver to the browser.
  const browser = await chromium.connectOverCDP(playwrightopts);
  const context = await browser.newContext({});
  const page = await context.newPage();
 
  // Go to hackernews home page.
  await page.goto("https://news.ycombinator.com/");
 
  // Find the search box at the bottom of the page and type the term lightpanda
  // to search.
  await page.locator('input[name="q"]').fill('lightpanda');
  // Press enter key to run the search.
  await page.keyboard.press('Enter');
 
  // Wait until the search results are loaded on the page, with a 5 seconds
  // timeout limit.
  await page.waitForSelector('.Story_container', { timeout: 5000 });
 
  // Loop over search results to extract data.
  const res = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('.Story_container')).map(row => {
      return {
        // Extract the title.
        title: row.querySelector('.Story_title span').textContent,
        // Extract the URL.
        url: row.querySelector('.Story_title a').getAttribute('href'),
        // Extract the list of meta data.
        meta: Array.from(row.querySelectorAll('.Story_meta > span:not(.Story_separator, .Story_comment)')).map(row => {
          return row.textContent;
        }),
      }
    });
  });
 
  // Display the result.
  console.log(res);
 
  // Disconnect Playwright.
  await page.close();
  await context.close();
  await browser.close();
})();
```

## Interested in on premise deployment?[Permalink for this section](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud#interested-in-on-premise-deployment)

The core Lightpanda browser will always remain open source, including JavaScript execution, CDP compatibility, proxy support, and request interception.

If you require on premise deployment, proprietary licensing, or enterprise features such as multi\-context tabs and sandboxing, reach out to us at [hello@lightpanda.io](mailto:hello@lightpanda.io).

## Need help?[Permalink for this section](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud#need-help)

Stuck or have questions about your use case? Open an issue on GitHub or [join our Discord ](https://discord.com/invite/K63XeymfB5).
[3. Extract data](https://lightpanda.io/docs/quickstart/build-your-first-extraction-script)
[Installation](https://lightpanda.io/docs/open-source/installation)
---

Built with [Nextra](https://nextra.site)
