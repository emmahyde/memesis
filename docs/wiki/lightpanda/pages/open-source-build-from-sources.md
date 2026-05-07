[Skip to Content](https://lightpanda.io/docs/open-source/guides/build-from-sources#nextra-skip-nav)
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
- [Prerequisites](https://lightpanda.io/docs/open-source/guides/build-from-sources#prerequisites)
- [Build and run](https://lightpanda.io/docs/open-source/guides/build-from-sources#build-and-run)
- [Embed v8 snapshot](https://lightpanda.io/docs/open-source/guides/build-from-sources#embed-v8-snapshot)
[Question? Send us feedback ](https://github.com/lightpanda-io/docs/issues/new?title=Feedback%20for%20%E2%80%9CBuild%20from%20sources%E2%80%9D&labels=feedback)
[Edit this page ](https://github.com/lightpanda-io/docs/blob/main/src/content/open-source/guides/build-from-sources.mdx)
Version: 0.2.9
[](https://www.linkedin.com/company/102175668)[](https://x.com/lightpanda_io)[](https://discord.gg/K63XeymfB5)[](https://github.com/lightpanda-io/browser)Scroll to top
[Open source edition](https://lightpanda.io/docs/open-source/installation)
GuidesBuild from sources

# Build from sources

## Prerequisites[Permalink for this section](https://lightpanda.io/docs/open-source/guides/build-from-sources#prerequisites)

Lightpanda is written with [Zig ](https://ziglang.org/) `0.14.0`. You will have to install it with the right version in order to build the project.

You need also to install [Rust ](https://rust-lang.org/tools/install/) for building deps.

Lightpanda also depends on [zig\-js\-runtime ](https://github.com/lightpanda-io/zig-js-runtime/) \(with v8\), [Libcurl ](https://curl.se/libcurl/) and [html5ever ](https://github.com/servo/html5ever).

To be able to build the v8 engine for zig\-js\-runtime, you have to install some libs:

**For Debian/Ubuntu based Linux:**

```
sudo apt install xz-utils ca-certificates \
        pkg-config libglib2.0-dev \
        clang make curl git
```

**For MacOS, you need [Xcode ](https://developer.apple.com/xcode/) and the following pacakges from homebrew:**

```
brew install cmake
```

## Build and run[Permalink for this section](https://lightpanda.io/docs/open-source/guides/build-from-sources#build-and-run)

You an build the entire browser with `make build` or `make build\-dev` for debug env.

But you can directly use the zig command to run in debug mode:

```
zig build run
```
ℹ️

The build will download and build V8. It can takes a lot of time, more than 1 hour. You can save this part by donwloading manually a \[pre\-built\]\([https://github.com/lightpanda\-io/zig\-v8\-fork/releases ](https://github.com/lightpanda-io/zig-v8-fork/releases) version\) and use the `\-Dprebuilt\_v8\_path=` option.

### Embed v8 snapshot[Permalink for this section](https://lightpanda.io/docs/open-source/guides/build-from-sources#embed-v8-snapshot)

Lighpanda uses v8 snapshot. By default, it is created on startup but you can embed it by using the following commands:

Generate the snapshot.

```
zig build snapshot_creator -- src/snapshot.bin
```

Build using the snapshot binary.

```
zig build -Dsnapshot_path=../../snapshot.bin
```

See [\#1279 ](https://github.com/lightpanda-io/browser/pull/1279) for more details.
[Usage](https://lightpanda.io/docs/open-source/usage)
[Configure a proxy](https://lightpanda.io/docs/open-source/guides/configure-a-proxy)
---

Built with [Nextra](https://nextra.site)
