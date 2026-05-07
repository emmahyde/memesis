[Skip to Content](https://lightpanda.io/docs/open-source/installation#nextra-skip-nav)
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
- [One\-liner installer](https://lightpanda.io/docs/open-source/installation#one-liner-installer)
- [Install from Docker](https://lightpanda.io/docs/open-source/installation#install-from-docker)
- [Install manually from the nightly builds](https://lightpanda.io/docs/open-source/installation#install-manually-from-the-nightly-builds)
- [Linux x86\_64](https://lightpanda.io/docs/open-source/installation#linux-x86_64)
- [Linux aarch64](https://lightpanda.io/docs/open-source/installation#linux-aarch64)
- [MacOS aarch64](https://lightpanda.io/docs/open-source/installation#macos-aarch64)
- [MacOS x86\_64](https://lightpanda.io/docs/open-source/installation#macos-x86_64)
- [Windows \+ WSL2](https://lightpanda.io/docs/open-source/installation#windows--wsl2)
- [Telemetry](https://lightpanda.io/docs/open-source/installation#telemetry)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CInstallation%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/open-source/installation.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top
Open source editionInstallation

# Installation

## One\-liner installer[Permalink for this section](https://lightpanda.io/docs/open-source/installation#one-liner-installer)

For Linux or MacOSx users, you can install Lightpanda with following command. For Windows, take a look at the [dedicated section](https://lightpanda.io/docs/open-source/installation#windows--wsl2).

```
curl -fsSL https://pkg.lightpanda.io/install.sh | bash
```
ℹ️

`curl`, `jq` and `sha256sum` are required to install Lightpanda with the one\-liner installer.

By default the installer installs the last nightly build. But you can pick a specific release:

```
curl -fsSL https://pkg.lightpanda.io/install.sh | bash -s "v0.2.5"
```

## Install from Docker[Permalink for this section](https://lightpanda.io/docs/open-source/installation#install-from-docker)

Lightpanda provides [official Docker images ](https://hub.docker.com/r/lightpanda/browser) for both Linux amd64 and arm64 architectures.

The following command fetches the Docker image and starts a new container exposing Lightpanda’s CDP server on port `9222`.

```
docker run -d --name lightpanda -p 127.0.0.1:9222:9222 lightpanda/browser:nightly
```

## Install manually from the nightly builds[Permalink for this section](https://lightpanda.io/docs/open-source/installation#install-manually-from-the-nightly-builds)

The latest binary can be downloaded from the [nightly builds ](https://github.com/lightpanda-io/browser/releases/tag/nightly) for Linux and MacOS.

### Linux x86\_64[Permalink for this section](https://lightpanda.io/docs/open-source/installation#linux-x86_64)

```
curl -L -o lightpanda \
  https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux && \
  chmod a+x ./lightpanda
```

### Linux aarch64[Permalink for this section](https://lightpanda.io/docs/open-source/installation#linux-aarch64)

```
curl -L -o lightpanda \
  https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-aarch64-linux && \
  chmod a+x ./lightpanda
```

### MacOS aarch64[Permalink for this section](https://lightpanda.io/docs/open-source/installation#macos-aarch64)

```
curl -L -o lightpanda \
  https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-aarch64-macos && \
  chmod a+x ./lightpanda
```

### MacOS x86\_64[Permalink for this section](https://lightpanda.io/docs/open-source/installation#macos-x86_64)

```
curl -L -o lightpanda \
  https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-macos && \
  chmod a+x ./lightpanda
```

## Windows \+ WSL2[Permalink for this section](https://lightpanda.io/docs/open-source/installation#windows--wsl2)

The Lightpanda browser is compatible to run on Windows inside WSL \(Windows Subsystem for Linux\). If WSL has not been installed before follow these steps \(for more information see: [MS Windows install WSL ](https://learn.microsoft.com/en-us/windows/wsl/install)\). Install & open WSL \+ Ubuntu from an **administrator** shell:
1. `wsl \-\-install` 
2. — restart — 
3. `wsl \-\-install \-d Ubuntu` 
4. `wsl` 

Once WSL and a Linux distribution have been installed the browser can be installed in the same way it is installed for Linux. Inside WSL install the Lightpanda browser:

```
curl -L -o lightpanda https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux && \
chmod a+x ./lightpanda
```

It is recommended to install clients like Puppeteer on the Windows host.

## Telemetry[Permalink for this section](https://lightpanda.io/docs/open-source/installation#telemetry)

By default, Lightpanda collects and sends usage telemetry. This can be disabled by setting an environment variable `LIGHTPANDA\_DISABLE\_TELEMETRY=true`. You can read Lightpanda’s privacy policy at: [https://lightpanda.io/privacy\-policy ](https://lightpanda.io/privacy-policy).
[4. Go to production](https://lightpanda.io/docs/quickstart/go-to-production-with-lightpanda-cloud)
[Usage](https://lightpanda.io/docs/open-source/usage)
---

Built with [Nextra](https://nextra.site)
