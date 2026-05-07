[Skip to Content](https://lightpanda.io/docs/open-source/guides/use-stagehand#nextra-skip-nav)
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
- [Install the Lightanda and Stagehand dependencies](https://lightpanda.io/docs/open-source/guides/use-stagehand#install-the-lightanda-and-stagehand-dependencies)
- [Write your Stagehand script with Lightpanda](https://lightpanda.io/docs/open-source/guides/use-stagehand#write-your-stagehand-script-with-lightpanda)
- [Run your script](https://lightpanda.io/docs/open-source/guides/use-stagehand#run-your-script)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CUse%20Stagehand%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/open-source/guides/use-stagehand.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top
[Open source edition](https://lightpanda.io/docs/open-source/installation)
[Guides](https://lightpanda.io/docs/open-source/guides/build-from-sources)
Use Stagehand

# Use Stagehand with Lightpanda

[Stagehand ](https://www.stagehand.dev/) is a popular, [open source ](https://github.com/browserbase/stagehand) AI Browser Automation Framework.

With Stagehand you can use natural language and code to control browser.

Since Lightpanda supports [Accessibilty tree ](https://github.com/lightpanda-io/browser/pull/1308), you can use it instead of Chrome with your Stagehand script.

## Install the Lightanda and Stagehand dependencies[Permalink for this section](https://lightpanda.io/docs/open-source/guides/use-stagehand#install-the-lightanda-and-stagehand-dependencies)

If not set, create a new npm project and install Stagehand depencies.

```
npm init
```

```
npm install @browserbasehq/stagehand @lightpanda/browser
```

## Write your Stagehand script with Lightpanda[Permalink for this section](https://lightpanda.io/docs/open-source/guides/use-stagehand#write-your-stagehand-script-with-lightpanda)

Now you can create your Stagehand’s. script to connectm by editing `index.js` file.

```
'use strict'
 
import { lightpanda } from '@lightpanda/browser';
 
import { Stagehand } from "@browserbasehq/stagehand";
import { z } from "zod/v3";
 
const lpdopts = { host: '127.0.0.1', port: 9222 };
 
const stagehandopts = {
  // Enable LOCAL env to configure the CDP url manually in the launch options.
  env: "LOCAL",
  localBrowserLaunchOptions: {
      cdpUrl: 'ws://' + lpdopts.host + ':' + lpdopts.port,
  },
  // You need an ANTHROPIC_API_KEY env var.
  model: "anthropic/claude-haiku-4-5",
  verbose: 0,
};
 
(async () => {
  // Start Lightpanda browser in a separate process.
  const proc = await lightpanda.serve(lpdopts);
 
  try {
    // Connect Stagehand to the browser.
    const stagehand = new Stagehand(stagehandopts);
 
    await stagehand.init();
 
    // Impportant: in the official documentation, Stagehand uses the default
    // existing page. But Lightpanda requires an explicit page's creation
    // instead.
    const page = await stagehand.context.newPage();
 
    await page.goto('https://demo-browser.lightpanda.io/amiibo/', {waitUntil: "networkidle"});
    const name = await stagehand.extract("Extract character's name", z.string());
    console.log("===", name);
 
    await stagehand.close()
 
  } finally {
    // Stop Lightpanda browser process.
    proc.stdout.destroy();
    proc.stderr.destroy();
    proc.kill();
  }
})();
```

## Run your script[Permalink for this section](https://lightpanda.io/docs/open-source/guides/use-stagehand#run-your-script)

Before running you script, make sure you have a valid Anthropic api key exported into the env var `ANTHROPIC\_API\_KEY`. You can also use [another model ](https://docs.stagehand.dev/v3/configuration/models) supported by Stagehand.

```
node index.js
```

You should see in the following logs:

```
=== Sandy
```
[Native MCP server](https://lightpanda.io/docs/open-source/guides/mcp-server)
[Systems requirements](https://lightpanda.io/docs/open-source/systems-requirements)
---

Built with [Nextra](https://nextra.site)
