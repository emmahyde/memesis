[Skip to Content](https://lightpanda.io/docs/cloud-offer/getting-started#nextra-skip-nav)
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
- [Create an account](https://lightpanda.io/docs/cloud-offer/getting-started#create-an-account)
- [Start using a browser](https://lightpanda.io/docs/cloud-offer/getting-started#start-using-a-browser)
- [Sign in to the dashboard](https://lightpanda.io/docs/cloud-offer/getting-started#sign-in-to-the-dashboard)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CGetting%20started%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/cloud-offer/getting-started.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top
Cloud offerGetting started

# Getting started

## Create an account[Permalink for this section](https://lightpanda.io/docs/cloud-offer/getting-started#create-an-account)

You can create a new account with an email on [https://lightpanda.io ](https://lightpanda.io/#cloud-offer).

You will receive an invitation by email to generate your token. Be careful to save your token, we won’t display it again.

## Start using a browser[Permalink for this section](https://lightpanda.io/docs/cloud-offer/getting-started#start-using-a-browser)

With your token, you can immediately use a remote browser with your CDP client.

Example using [Playwright ](https://playwright.dev/).

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

You have access to Lightpanda and Chromium browsers.
ℹ️

Depending on your location, you can connect using the url `wss://euwest.cloud.lightpanda.io/ws` or `wss//uswest.cloud.lightpanda.io/ws`.

## Sign in to the dashboard[Permalink for this section](https://lightpanda.io/docs/cloud-offer/getting-started#sign-in-to-the-dashboard)

You can access your dashboard on [https://console.lightpanda.io ](https://console.lightpanda.io).

Use your email and your token to log in.

In the dashboard, you can review your last browsing sessions.
[Systems requirements](https://lightpanda.io/docs/open-source/systems-requirements)
[CDP](https://lightpanda.io/docs/cloud-offer/tools/cdp)
---

Built with [Nextra](https://nextra.site)
