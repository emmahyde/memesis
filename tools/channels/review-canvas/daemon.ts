#!/usr/bin/env bun
/**
 * review-canvas daemon — persistent HTTP+WebSocket server.
 * Runs independently of the MCP stdio lifecycle.
 * HTTP on 127.0.0.1:8788. Killed explicitly; never exits on stdin close.
 */

import { readFileSync } from 'fs'
import type { ServerWebSocket } from 'bun'

const PORT = Number(process.env.REVIEW_CANVAS_PORT ?? 8788)

let currentPath = process.env.REVIEW_CANVAS_TRANSCRIPT ?? ''

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Turn = { idx: number; role: 'user' | 'assistant'; ts: string; text: string }
type Comment = { id: string; turn_idx: number; line_start: number; line_end: number; quote: string; text: string }
type PendingComment = Comment & { createdAt: number }

// ---------------------------------------------------------------------------
// Transcript loading (verbatim from server.ts)
// ---------------------------------------------------------------------------

function extractText(content: unknown): string {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .filter((blk: any) => blk?.type === 'text')
    .map((blk: any) => blk.text ?? '')
    .join('\n\n')
}

function loadTranscript(path: string): Turn[] {
  if (!path) return []
  const raw = readFileSync(path, 'utf8')
  if (path.endsWith('.md') || path.endsWith('.txt')) {
    const text = raw.trim()
    return text ? [{ idx: 0, role: 'assistant', ts: '', text }] : []
  }
  const turns: Turn[] = []
  let idx = 0
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue
    let obj: any
    try { obj = JSON.parse(line) } catch { continue }
    if (!obj || typeof obj !== 'object') continue
    const { type: t, message: msg, timestamp } = obj
    if (t !== 'user' && t !== 'assistant') continue
    if (!msg) continue
    const text = extractText(msg.content).trim()
    if (!text || text.startsWith('<local-command-')) continue
    turns.push({ idx: idx++, role: t, ts: timestamp ?? '', text })
  }
  return turns
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let turns = loadTranscript(currentPath)

const wsClients = new Set<ServerWebSocket<{ route: string }>>()
let bridgeWs: ServerWebSocket<{ route: string }> | null = null

// ---------------------------------------------------------------------------
// Pending comments queue
// ---------------------------------------------------------------------------

const pendingComments: PendingComment[] = []
const QUEUE_CAP = 50
const QUEUE_TTL = 10 * 60 * 1000

function enqueuePending(c: Comment) {
  const now = Date.now()
  while (pendingComments.length > 0 && now - pendingComments[0].createdAt > QUEUE_TTL) pendingComments.shift()
  if (pendingComments.length >= QUEUE_CAP) pendingComments.shift()
  pendingComments.push({ ...c, createdAt: now })
}

// ---------------------------------------------------------------------------
// Broadcast helpers
// ---------------------------------------------------------------------------

function broadcastWs(o: unknown) {
  const data = JSON.stringify(o)
  for (const ws of wsClients) if (ws.readyState === WebSocket.OPEN) ws.send(data)
}

// ---------------------------------------------------------------------------
// Reload
// ---------------------------------------------------------------------------

function reloadTranscript(path: string) {
  currentPath = path
  turns = loadTranscript(path)
  broadcastWs({ type: 'reload', turns, path: currentPath })
}

// ---------------------------------------------------------------------------
// HTML (verbatim from server.ts)
// ---------------------------------------------------------------------------

const HTML = `<!doctype html>
<meta charset="utf-8">
<title>review-canvas</title>
<style>
:root {
  --bg: #fafafa; --bg-elev: #ffffff; --bg-hover: #f0f4ff;
  --fg: #1f2328; --fg-muted: #6e7781; --fg-faint: #afb8c1;
  --border: #d8dee4; --border-soft: #eaeef2;
  --user: #0969da; --asst: #8250df;
  --user-bg: #ddf4ff; --asst-bg: #fbefff;
  --sel: #fff8c5; --hl: #fff8c5;
  --code-bg: #f6f8fa;
  --comment-bg: #fff8c5; --comment-border: #d4a017;
  --reply-bg: #ddf4ff;
  --md-heading: #0550ae; --md-list: #8250df; --md-link: #0969da;
  --md-emph: #cf222e; --md-code: #cf222e; --md-fence: #6e7781;
  --md-quote: #1a7f37; --md-bold: #24292f;
  --shadow: 0 1px 3px rgba(0,0,0,.04);
  --shadow-lg: 0 4px 12px rgba(0,0,0,.06);
}
[data-theme="dark"] {
  --bg: #0d1117; --bg-elev: #161b22; --bg-hover: #21262d;
  --fg: #e6edf3; --fg-muted: #7d8590; --fg-faint: #484f58;
  --border: #30363d; --border-soft: #21262d;
  --user: #58a6ff; --asst: #bc8cff;
  --user-bg: #0c2d6b33; --asst-bg: #4d287633;
  --sel: #3a3520; --hl: #3a3520;
  --code-bg: #161b22;
  --comment-bg: #2d2a1a; --comment-border: #d4a017;
  --reply-bg: #0c2d6b33;
  --md-heading: #79c0ff; --md-list: #bc8cff; --md-link: #58a6ff;
  --md-emph: #ff7b72; --md-code: #ff7b72; --md-fence: #7d8590;
  --md-quote: #7ee787; --md-bold: #f0f6fc;
  --shadow: 0 1px 3px rgba(0,0,0,.3);
  --shadow-lg: 0 4px 12px rgba(0,0,0,.4);
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: var(--bg); color: var(--fg);
  font-size: 14px; line-height: 1.5;
  transition: background .15s, color .15s;
}
.mono { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; }
header {
  position: sticky; top: 0; z-index: 10;
  background: var(--bg-elev); border-bottom: 1px solid var(--border);
  padding: .7em 1.2em; display: flex; align-items: center; gap: 1em;
  box-shadow: var(--shadow);
}
header h1 { margin: 0; font-size: 14px; font-weight: 600; letter-spacing: -0.01em; }
header .meta { color: var(--fg-muted); font-size: 12px; flex: 1; font-family: ui-monospace, monospace; }
header .path { color: var(--fg-muted); font-size: 11px; font-family: ui-monospace, monospace; opacity: .8; }
header button {
  font: inherit; font-size: 12px; padding: .35em .8em;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--bg-elev); color: var(--fg); cursor: pointer;
  transition: all .15s;
}
header button:hover { background: var(--bg-hover); border-color: var(--fg-faint); }
main { max-width: 960px; margin: 0 auto; padding: 1.2em; }
.turn {
  background: var(--bg-elev); border: 1px solid var(--border); border-radius: 8px;
  margin-bottom: 1em; overflow: hidden; box-shadow: var(--shadow);
}
.turn-head {
  padding: .5em .9em; border-bottom: 1px solid var(--border-soft);
  font-size: 12px; display: flex; justify-content: space-between;
  font-family: ui-monospace, monospace;
}
.turn-head.user { background: var(--user-bg); color: var(--user); }
.turn-head.assistant { background: var(--asst-bg); color: var(--asst); }
.turn pre { margin: 0; padding: .3em 0; white-space: pre-wrap; word-break: break-word; font-family: inherit; font-size: 14px; }
.line.code-line .src, .md-code-block, .md-fence { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: 13px; }
.line.md-h1 .src { font-size: 1.6em; font-weight: 700; color: var(--md-heading); line-height: 1.3; padding: .3em 0 .15em 0; }
.line.md-h2 .src { font-size: 1.35em; font-weight: 700; color: var(--md-heading); padding: .25em 0 .1em 0; }
.line.md-h3 .src { font-size: 1.18em; font-weight: 700; color: var(--md-heading); padding: .2em 0 .05em 0; }
.line.md-h4 .src, .line.md-h5 .src, .line.md-h6 .src { font-weight: 700; color: var(--md-heading); }
.line .src strong { font-weight: 700; color: var(--md-bold); }
.line .src em { font-style: italic; color: var(--md-emph); }
.line .src code { font-family: ui-monospace, monospace; background: var(--code-bg); padding: 0 .25em; border-radius: 3px; font-size: .88em; color: var(--md-code); }
.line .src a.md-link { color: var(--md-link); text-decoration: underline; cursor: pointer; }
.mermaid-block { display: block; padding: .8em .9em; background: var(--code-bg); border-radius: 4px; margin: .4em 0; }
.mermaid-block .mermaid { width: 100%; min-height: 60px; }
.mermaid-block .mermaid svg { width: 100% !important; height: auto !important; max-width: 100%; display: block; margin: 0 auto; }
.line { display: flex; padding: 0 .9em; cursor: text; transition: background .08s; }
.line:hover { background: var(--bg-hover); }
.line.sel { background: var(--sel); }
.line .num {
  min-width: 5ch; color: var(--fg-faint); text-align: right;
  padding-right: 1em; user-select: none; flex-shrink: 0;
  font-size: 12px; white-space: nowrap;
}
.line.md-table-row { background: var(--code-bg); }
.line.md-table-row:nth-of-type(odd) { background: transparent; }
.md-pipe { color: var(--fg-faint); }
.md-table-sep { color: var(--fg-faint); }
/* Prism overrides — match our theme */
.token.comment, .token.prolog, .token.doctype, .token.cdata { color: var(--fg-muted); font-style: italic; }
.token.punctuation { color: var(--fg-muted); }
.token.property, .token.tag, .token.boolean, .token.number, .token.constant, .token.symbol, .token.deleted { color: var(--md-emph); }
.token.selector, .token.attr-name, .token.string, .token.char, .token.builtin, .token.inserted { color: var(--md-quote); }
.token.operator, .token.entity, .token.url, .language-css .token.string, .style .token.string { color: var(--md-link); }
.token.atrule, .token.attr-value, .token.keyword { color: var(--md-list); font-weight: 600; }
.token.function, .token.class-name { color: var(--md-link); }
.token.regex, .token.important, .token.variable { color: var(--md-emph); }
.line .src { flex: 1; min-width: 0; }
.composer {
  position: sticky; bottom: 0; z-index: 10;
  background: var(--bg-elev); border-top: 2px solid var(--user);
  padding: .8em 1.2em; box-shadow: var(--shadow-lg);
}
.composer.hidden { display: none; }
.composer .quote {
  background: var(--code-bg); border-left: 3px solid var(--fg-faint);
  padding: .5em .7em; margin-bottom: .5em;
  max-height: 6em; overflow: auto; font-size: 12px;
  font-family: ui-monospace, monospace; border-radius: 0 4px 4px 0;
}
.composer textarea {
  width: 100%; font: inherit; padding: .5em .7em;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--bg); color: var(--fg);
  resize: vertical; min-height: 3em;
}
.composer textarea:focus { outline: 2px solid var(--user); outline-offset: -1px; border-color: var(--user); }
.composer .row { display: flex; gap: .6em; margin-top: .5em; align-items: center; }
.composer button {
  font: inherit; font-size: 13px; padding: .4em 1em;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--bg-elev); color: var(--fg); cursor: pointer;
  transition: all .15s;
}
.composer button:hover { background: var(--bg-hover); }
.composer button.send { background: var(--user); color: #fff; border-color: var(--user); }
.composer button.send:hover { filter: brightness(1.1); }
.composer .info { color: var(--fg-muted); font-size: 12px; flex: 1; font-family: ui-monospace, monospace; }
.comment {
  background: var(--comment-bg); border-left: 3px solid var(--comment-border);
  padding: .6em .9em; margin: .4em .9em; border-radius: 0 4px 4px 0;
  font-size: 13px;
}
.comment .by { color: var(--fg-muted); font-size: 11px; font-family: ui-monospace, monospace; margin-bottom: .3em; }
.comment .body { white-space: pre-wrap; }
.comment .reply {
  background: var(--reply-bg); border-left: 3px solid var(--user);
  margin-top: .5em; padding: .5em .7em;
  border-radius: 0 4px 4px 0; white-space: pre-wrap;
}
.comment .reply.pending { color: var(--fg-muted); font-style: italic; }

/* Markdown syntax */
.md-heading { color: var(--md-heading); font-weight: 600; }
.md-list { color: var(--md-list); font-weight: 600; }
.md-link { color: var(--md-link); }
.md-bold { color: var(--md-bold); font-weight: 700; }
.md-italic { color: var(--md-emph); font-style: italic; }
.md-code { color: var(--md-code); background: var(--code-bg); padding: 0 .2em; border-radius: 3px; }
.md-fence { color: var(--md-fence); }
.md-code-block { color: var(--md-code); background: var(--code-bg); display: inline-block; min-width: 100%; }
.md-quote { color: var(--md-quote); font-style: italic; }
</style>
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/prism.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-python.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-typescript.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-bash.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-json.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-yaml.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-rust.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-go.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/plugins/autoloader/prism-autoloader.min.js"></script>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs'
  window._mermaid = mermaid
  const t = localStorage.getItem('theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  mermaid.initialize({ startOnLoad: false, theme: t === 'dark' ? 'dark' : 'neutral', suppressErrorRendering: false })
  window._mermaidTheme = t
  document.dispatchEvent(new Event('mermaid-ready'))
</script>
<header>
  <h1>review-canvas</h1>
  <div class="meta" id="meta">loading…</div>
  <span class="path" id="path"></span>
  <button id="theme-toggle" title="Toggle theme">theme</button>
</header>
<main id="root"></main>
<div class="composer hidden" id="composer">
  <div class="quote" id="quote"></div>
  <textarea id="text" placeholder="Comment on selected lines… (Cmd+Enter to send)"></textarea>
  <div class="row">
    <span class="info" id="info"></span>
    <button onclick="cancelSel()">cancel</button>
    <button class="send" onclick="sendComment()">send</button>
  </div>
</div>
<script>
let turns = []
let sel = null
let isDragging = false
let dragAnchor = null
const commentsByLoc = {}

// Theme
function applyTheme(t) {
  document.documentElement.dataset.theme = t
  localStorage.setItem('theme', t)
  document.getElementById('theme-toggle').textContent = t === 'dark' ? 'light' : 'dark'
  if (window._mermaid && turns.length) {
    window._mermaid.initialize({ startOnLoad: false, theme: t === 'dark' ? 'dark' : 'neutral' })
    render()
    runMermaid()
  }
}
const storedTheme = localStorage.getItem('theme')
const initialTheme = storedTheme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
applyTheme(initialTheme)
document.getElementById('theme-toggle').onclick = () => {
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark')
}

// Markdown highlighting
function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
function renderInline(text) {
  const codes = []
  text = text.replace(/\`([^\`]+)\`/g, (_, c) => { codes.push(escHtml(c)); return '\\u0001CODE' + (codes.length-1) + '\\u0001' })
  text = escHtml(text)
  text = text.replace(/\\*\\*([^*\\n]+)\\*\\*/g, '<strong>$1</strong>')
  text = text.replace(/(?<!\\*)\\*([^*\\n]+)\\*(?!\\*)/g, '<em>$1</em>')
  text = text.replace(/(?<![A-Za-z0-9_])_([^_\\n]+)_(?![A-Za-z0-9_])/g, '<em>$1</em>')
  text = text.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a class="md-link" href="$2" target="_blank">$1</a>')
  text = text.replace(/\\u0001CODE(\\d+)\\u0001/g, (_, i) => '<code>' + codes[+i] + '</code>')
  return text
}
function highlightTableRow(line) {
  const isSep = /^\\s*\\|?[\\s\\-:|]+\\|?\\s*$/.test(line) && line.includes('-')
  if (isSep) return '<span class="md-table-sep">' + escHtml(line) + '</span>'
  // Render cells with inline markdown; keep pipe separators dim
  return line.split('|').map((cell, ci) => {
    if (ci === 0 || ci === line.split('|').length - 1) return ''
    return renderInline(cell) + '<span class="md-pipe">|</span>'
  }).filter((_, ci, arr) => ci < arr.length - 1).join('') || escHtml(line)
}
function buildTableWidths(lines) {
  const result = new Map()
  const isTableLine = ln => /^\\s*\\|/.test(ln) || (ln.match(/\\|/g) || []).length >= 2
  let i = 0
  while (i < lines.length) {
    if (!isTableLine(lines[i])) { i++; continue }
    let j = i
    while (j < lines.length && isTableLine(lines[j])) j++
    const widths = []
    for (let k = i; k < j; k++) {
      const isSep = /^\\s*\\|?[\\s\\-:|]+\\|?\\s*$/.test(lines[k]) && lines[k].includes('-')
      if (isSep) continue
      lines[k].split('|').forEach((c, ci) => {
        widths[ci] = Math.max(widths[ci] || 0, c.trim().length)
      })
    }
    for (let k = i; k < j; k++) result.set(k, widths)
    i = j
  }
  return result
}
function padTableLine(line, widths) {
  const cells = line.split('|')
  return cells.map((c, ci) => {
    if (ci === 0 || ci === cells.length - 1) return c
    return ' ' + c.trim().padEnd(widths[ci] || 0) + ' '
  }).join('|')
}
function highlightCodeLine(line, lang) {
  if (lang && window.Prism && Prism.languages[lang]) {
    try { return Prism.highlight(line, Prism.languages[lang], lang) } catch (e) {}
  }
  return escHtml(line)
}
function highlightLine(line, fence) {
  if (fence.inFence) {
    if (/^\\s*\`\`\`/.test(line)) { fence.inFence = false; fence.lang = null; return { html: '<span class="md-fence">' + escHtml(line) + '</span>', cls: 'code-line' } }
    return { html: '<span class="md-code-block">' + highlightCodeLine(line, fence.lang) + '</span>', cls: 'code-line' }
  }
  const fenceOpen = line.match(/^\\s*\`\`\`(\\w*)/)
  if (fenceOpen) { fence.inFence = true; fence.lang = fenceOpen[1] || null; return { html: '<span class="md-fence">' + escHtml(line) + '</span>', cls: 'code-line' } }
  if (!line.trim()) return { html: '', cls: '' }
  if (/^\\s*\\|/.test(line) || (line.match(/\\|/g) || []).length >= 2) {
    return { html: highlightTableRow(line), cls: '' }
  }
  const heading = line.match(/^(#{1,6})\\s+(.+)$/)
  if (heading) {
    const level = heading[1].length
    return { html: renderInline(heading[2]), cls: 'md-h' + level }
  }
  if (/^\\s*>\\s/.test(line)) return { html: '<span class="md-quote">' + renderInline(line.replace(/^\\s*>\\s/, '')) + '</span>', cls: '' }
  const listMatch = line.match(/^(\\s*)([-*+]|\\d+\\.)(\\s)(.*)$/)
  if (listMatch) return { html: listMatch[1] + '<span class="md-list">' + listMatch[2] + '</span>' + listMatch[3] + renderInline(listMatch[4]), cls: '' }
  return { html: renderInline(line), cls: '' }
}

function buildMermaidBlocks(lines) {
  const blocks = new Map()
  let i = 0
  while (i < lines.length) {
    if (/^\\s*\`\`\`mermaid\\s*$/.test(lines[i])) {
      const start = i
      const contentLines = []
      i++
      while (i < lines.length && !/^\\s*\`\`\`\\s*$/.test(lines[i])) {
        contentLines.push(lines[i])
        i++
      }
      const end = i
      const blockContent = contentLines.join('\\n')
      blocks.set(start, { type: 'start', blockEnd: end, content: blockContent })
      for (let k = start + 1; k < end; k++) blocks.set(k, { type: 'inner' })
      if (i < lines.length) blocks.set(end, { type: 'end' })
      i++
    } else { i++ }
  }
  return blocks
}
async function runMermaid() {
  if (!window._mermaid) {
    await new Promise(r => document.addEventListener('mermaid-ready', r, { once: true }))
  }
  const nodes = [...document.querySelectorAll('.mermaid:not([data-processed="true"])')]
  for (const node of nodes) {
    try {
      await window._mermaid.run({ nodes: [node] })
    } catch(e) {
      console.error('mermaid render error for block:', e.message)
      console.error('block content:', node.textContent.slice(0, 200))
    }
  }
}

async function load() {
  turns = await fetch('/turns').then(r => r.json())
  document.getElementById('meta').textContent = turns.length + ' turns'
  const status = await fetch('/status').then(r => r.json()).catch(() => null)
  if (status && status.path) document.getElementById('path').textContent = status.path
  render()
  runMermaid()
  connectWS()
}

function render() {
  const root = document.getElementById('root')
  root.innerHTML = ''
  for (const t of turns) {
    const div = document.createElement('div')
    div.className = 'turn'
    div.dataset.idx = t.idx
    const head = document.createElement('div')
    head.className = 'turn-head ' + t.role
    head.innerHTML = '<span>#' + t.idx + ' · ' + t.role + '</span><span>' + (t.ts || '') + '</span>'
    div.appendChild(head)
    const pre = document.createElement('pre')
    const lines = t.text.split('\\n')
    const fence = { inFence: false, lang: null }
    const tableWidths = buildTableWidths(lines)
    const mermaidBlocks = buildMermaidBlocks(lines)
    let mermaidSkipUntil = -1
    lines.forEach((ln, i) => {
      // Skip inner + end lines of mermaid blocks (fence already reset; do NOT re-process)
      if (i <= mermaidSkipUntil) { return }
      const mb = mermaidBlocks.get(i)
      if (mb && mb.type === 'start') {
        mermaidSkipUntil = mb.blockEnd
        fence.inFence = false
        const mrow = document.createElement('div')
        mrow.className = 'line mermaid-block'
        mrow.dataset.turn = t.idx
        mrow.dataset.line = i + 1
        const mdiv = document.createElement('div')
        mdiv.className = 'mermaid'
        mdiv.textContent = mb.content
        mrow.appendChild(mdiv)
        mrow.addEventListener('mousedown', e2 => {
          e2.preventDefault()
          isDragging = true
          dragAnchor = { turn_idx: t.idx, line: i + 1 }
          sel = { turn_idx: t.idx, line_start: i + 1, line_end: mb.blockEnd + 1 }
          paintSel()
        })
        pre.appendChild(mrow)
        return
      }
      const row = document.createElement('div')
      row.className = 'line'
      const isTable = !fence.inFence && (/^\\s*\\|/.test(ln) || (ln.match(/\\|/g) || []).length >= 2)
      if (isTable) row.className += ' md-table-row'
      row.dataset.turn = t.idx
      row.dataset.line = i + 1
      row.innerHTML = '<span class="num">' + (i + 1) + '</span><span class="src"></span>'
      const isSepRow = /^\\s*\\|?[\\s\\-:|]+\\|?\\s*$/.test(ln) && ln.includes('-')
      const displayLn = (isTable && !isSepRow && tableWidths.has(i)) ? padTableLine(ln, tableWidths.get(i)) : ln
      const hl = highlightLine(displayLn, fence)
      const hlHtml = typeof hl === 'object' ? hl.html : hl
      const hlCls = typeof hl === 'object' ? hl.cls : ''
      if (hlCls) row.classList.add(hlCls)
      row.querySelector('.src').innerHTML = hlHtml || '&nbsp;'
      row.addEventListener('mousedown', e => {
        e.preventDefault()
        isDragging = true
        dragAnchor = { turn_idx: t.idx, line: i + 1 }
        sel = { turn_idx: t.idx, line_start: i + 1, line_end: i + 1 }
        paintSel()
      })
      row.addEventListener('mouseover', () => {
        if (!isDragging || !dragAnchor || dragAnchor.turn_idx !== t.idx) return
        sel = { turn_idx: t.idx, line_start: Math.min(dragAnchor.line, i + 1), line_end: Math.max(dragAnchor.line, i + 1) }
        paintSel()
      })
      pre.appendChild(row)
    })
    div.appendChild(pre)
    root.appendChild(div)
  }
}

document.addEventListener('mouseup', () => {
  if (isDragging && sel) showComposer()
  isDragging = false
  dragAnchor = null
})

function paintSel() {
  document.querySelectorAll('.line.sel').forEach(el => el.classList.remove('sel'))
  if (!sel) return
  for (let l = sel.line_start; l <= sel.line_end; l++) {
    const el = document.querySelector('.line[data-turn="' + sel.turn_idx + '"][data-line="' + l + '"]')
    if (el) el.classList.add('sel')
  }
}

function quoteText() {
  if (!sel) return ''
  const t = turns[sel.turn_idx]
  const lines = t.text.split('\\n').slice(sel.line_start - 1, sel.line_end)
  return lines.join('\\n')
}

function showComposer() {
  const c = document.getElementById('composer')
  c.classList.remove('hidden')
  document.getElementById('quote').textContent = quoteText()
  document.getElementById('info').textContent = 'turn #' + sel.turn_idx + ' · L' + sel.line_start + (sel.line_end !== sel.line_start ? '-L' + sel.line_end : '')
  document.getElementById('text').focus()
}

function cancelSel() {
  sel = null
  paintSel()
  document.getElementById('composer').classList.add('hidden')
  document.getElementById('text').value = ''
}

async function sendComment() {
  const text = document.getElementById('text').value.trim()
  if (!text || !sel) return
  const body = { turn_idx: sel.turn_idx, line_start: sel.line_start, line_end: sel.line_end, quote: quoteText(), text }
  await fetch('/comment', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
  cancelSel()
}

document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) sendComment()
  if (e.key === 'Escape') cancelSel()
})

function addCommentEl(c) {
  const turnEl = document.querySelector('.turn[data-idx="' + c.turn_idx + '"]')
  if (!turnEl) return
  const el = document.createElement('div')
  el.className = 'comment'
  el.dataset.id = c.id
  el.innerHTML = '<div class="by">L' + c.line_start + '-' + c.line_end + ' · you</div><div class="body"></div><div class="reply pending" data-reply>waiting for reply…</div>'
  el.querySelector('.body').textContent = c.text
  // Insert after the last selected line; fall back to turn-end
  const anchor = turnEl.querySelector('.line[data-line="' + c.line_end + '"]')
  if (anchor) anchor.insertAdjacentElement('afterend', el)
  else turnEl.appendChild(el)
  commentsByLoc[c.id] = el
}

function applyReply(comment_id, text) {
  const el = commentsByLoc[comment_id]
  if (!el) return
  const r = el.querySelector('[data-reply]')
  r.classList.remove('pending')
  r.innerHTML = '<b>claude:</b> '
  const span = document.createElement('span')
  span.textContent = text
  r.appendChild(span)
}

function connectWS() {
  const ws = new WebSocket('ws://' + location.host + '/ws')
  ws.onmessage = e => {
    const m = JSON.parse(e.data)
    if (m.type === 'reload') { turns = m.turns; if (m.path) document.getElementById('path').textContent = m.path; render(); runMermaid(); document.getElementById('meta').textContent = turns.length + ' turns' }
    if (m.type === 'comment') addCommentEl(m)
    if (m.type === 'reply') applyReply(m.comment_id, m.text)
  }
  ws.onclose = () => setTimeout(connectWS, 1000)
}

load()
</script>
`

// ---------------------------------------------------------------------------
// HTTP + WebSocket server
// ---------------------------------------------------------------------------

const server = Bun.serve<{ route: string }>({
  port: PORT,
  hostname: '127.0.0.1',
  idleTimeout: 0,

  fetch(req, server) {
    const url = new URL(req.url)

    if (url.pathname === '/ws') {
      if (server.upgrade(req, { data: { route: 'ws' } })) return
      return new Response('upgrade failed', { status: 400 })
    }

    if (url.pathname === '/bridge') {
      if (server.upgrade(req, { data: { route: 'bridge' } })) return
      return new Response('upgrade failed', { status: 400 })
    }

    if (url.pathname === '/') {
      return new Response(HTML, { headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store, must-revalidate' } })
    }

    if (url.pathname === '/turns') {
      return Response.json(turns)
    }

    if (url.pathname === '/status') {
      return Response.json({ turns: turns.length, path: currentPath, bridge_connected: bridgeWs !== null })
    }

    if (url.pathname === '/load' && req.method === 'POST') {
      return (async () => {
        const { path } = await req.json() as { path: string }
        if (!path) return new Response('path required', { status: 400 })
        try {
          reloadTranscript(path)
        } catch (e: any) {
          return new Response(String(e.message), { status: 422 })
        }
        return Response.json({ ok: true, turns: turns.length, path: currentPath })
      })()
    }

    if (url.pathname === '/comment' && req.method === 'POST') {
      return (async () => {
        const body = await req.json() as Omit<Comment, 'id'>
        const id = crypto.randomUUID()
        const c: Comment = { id, ...body }
        broadcastWs({ type: 'comment', ...c })
        if (bridgeWs && bridgeWs.readyState === WebSocket.OPEN) {
          bridgeWs.send(JSON.stringify({ type: 'comment', ...c }))
        } else {
          enqueuePending(c)
        }
        return Response.json({ id })
      })()
    }

    if (url.pathname === '/reply' && req.method === 'POST') {
      return (async () => {
        const { comment_id, text } = await req.json() as { comment_id: string; text: string }
        broadcastWs({ type: 'reply', comment_id, text, ts: Date.now() })
        return Response.json({ ok: true })
      })()
    }

    return new Response('404', { status: 404 })
  },

  websocket: {
    open(ws) {
      const route = ws.data?.route

      if (route === 'ws') {
        wsClients.add(ws)
      } else if (route === 'bridge') {
        // Evict prior bridge connection
        if (bridgeWs && bridgeWs.readyState === WebSocket.OPEN) {
          bridgeWs.close()
        }
        bridgeWs = ws

        // Drain pending comments queue
        const now = Date.now()
        const toSend = pendingComments.filter(c => now - c.createdAt <= QUEUE_TTL)
        for (const c of toSend) {
          const { createdAt: _, ...comment } = c
          ws.send(JSON.stringify({ type: 'comment', ...comment }))
        }
        pendingComments.length = 0
      }
    },

    close(ws) {
      if (wsClients.has(ws)) {
        wsClients.delete(ws)
      } else if (ws === bridgeWs) {
        bridgeWs = null
      }
    },

    message() {
      // no-op
    },
  },
})

process.stderr.write(`review-canvas daemon: http://localhost:${PORT} — ${turns.length} turns from ${currentPath || '(no file)'}\n`)
