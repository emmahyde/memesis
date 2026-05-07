[Skip to Content](https://lightpanda.io/docs/quickstart/installation-and-setup#nextra-skip-nav)
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
- [Prerequisites](https://lightpanda.io/docs/quickstart/installation-and-setup#prerequisites)
- [Initialize the Node.js project](https://lightpanda.io/docs/quickstart/installation-and-setup#initialize-the-nodejs-project)
- [Install Lightpanda dependency](https://lightpanda.io/docs/quickstart/installation-and-setup#install-lightpanda-dependency)
- [Step 2:  Your first test](https://lightpanda.io/docs/quickstart/installation-and-setup#step-2--your-first-test)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CInstallation%20and%20setup%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/quickstart/installation-and-setup.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top

# Quickstart

In this Quickstart, you’ll set up your first project with [Lightpanda browser ](https://lightpanda.io) and run it locally in under 10 minutes. By the end of this guide, you’ll have:
- A working [Node.js ](https://nodejs.org) project configured with Lightpanda 
- A browser instance that starts and stops programmatically 
- The foundation for running automated scripts using either [Puppeteer ](https://pptr.dev) or [Playwright ](https://playwright.dev/) to control the browser 
1. [Installation and setup](https://lightpanda.io/docs/quickstart/installation-and-setup) 
2. [Your first test](https://lightpanda.io/docs/quickstart/your-first-test) 
3. [Extract data](https://lightpanda.io/docs/quickstart/build-your-first-extraction-script) 
4. [Go to production with Lightpanda cloud](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud) 

# 1. Installation and setup

## Prerequisites[Permalink for this section](https://lightpanda.io/docs/quickstart/installation-and-setup#prerequisites)

You’ll need [Node.js ](https://nodejs.org/en/download) installed on your computer.

## Initialize the Node.js project[Permalink for this section](https://lightpanda.io/docs/quickstart/installation-and-setup#initialize-the-nodejs-project)

Create a `hn\-scraper` directory and initialize a new Node.js project.

```
mkdir hn-scraper && \
  cd hn-scraper && \
  npm init
```

You can accept all the default values in the npm init prompts. When done, your directory should look like this:
- hn\-scraper
  - package.json

## Install Lightpanda dependency[Permalink for this section](https://lightpanda.io/docs/quickstart/installation-and-setup#install-lightpanda-dependency)

Install Lightpanda by using the [official npm package ](https://www.npmjs.com/package/@lightpanda/browser).
npmyarnpnpm

### npm

```
npm install --save @lightpanda/browser
```

### yarn

```
yarn add @lightpanda/browser
```

### pnpm

```
pnpm add @lightpanda/browser
```

Create an `index.js` file with the following content:

```
'use strict'
 
import { lightpanda } from '@lightpanda/browser';
 
const lpdopts = {
  host: '127.0.0.1',
  port: 9222,
};
 
(async () => {
  // Start Lightpanda browser in a separate process.
  const proc = await lightpanda.serve(lpdopts);
 
  // Do your magic ✨
 
  // Stop Lightpanda browser process.
  proc.stdout.destroy();
  proc.stderr.destroy();
  proc.kill();
})();
```

Run your script to start and stop a Lightpanda browser.

```
node index.js
```

Starting and stopping the browser is almost instant.

```
$ node index.js
🐼 Running Lightpanda's CDP server... { pid: 4084512 }
```

### Step 2: [ Your first test](https://lightpanda.io/docs/quickstart/your-first-test)[Permalink for this section](https://lightpanda.io/docs/quickstart/installation-and-setup#step-2--your-first-test)
[Introduction](https://lightpanda.io/docs)
[2. Your first test](https://lightpanda.io/docs/quickstart/your-first-test)
---

Built with [Nextra](https://nextra.site)
