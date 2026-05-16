#!/usr/bin/env bun
/// <reference lib="dom" />
/**
 * review-canvas — MCP stdio bridge.
 *
 * Connects to the review-canvas daemon via WebSocket, receives comment events,
 * and delivers them to Claude via MCP notifications. Replies are POSTed back
 * to the daemon's /reply endpoint.
 *
 * HTTP server, WebSocket browser handling, and transcript loading live in daemon.ts.
 */

import { appendFileSync, readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from '@modelcontextprotocol/sdk/types.js'

const DAEMON_URL = `http://localhost:${process.env.REVIEW_CANVAS_PORT ?? 8788}`
const BRIDGE_WS_URL = `ws://localhost:${process.env.REVIEW_CANVAS_PORT ?? 8788}/bridge`

async function getDaemonStatus(): Promise<{ turns: number; path: string; bridge_connected: boolean } | null> {
  try {
    const resp = await fetch(`${DAEMON_URL}/status`)
    if (!resp.ok) return null
    return resp.json()
  } catch { return null }
}

const mcp = new Server(
  { name: 'review-canvas', version: '0.1.0' },
  {
    capabilities: { tools: {}, experimental: { 'claude/channel': {} } },
    instructions: `review-canvas bridge. UI at http://localhost:${process.env.REVIEW_CANVAS_PORT ?? 8788}. Connecting to daemon...`,
  },
)

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'reply',
      description: 'Respond to a review comment. Pass the comment_id you are answering and the response text.',
      inputSchema: {
        type: 'object',
        properties: {
          comment_id: { type: 'string' },
          text: { type: 'string' },
        },
        required: ['comment_id', 'text'],
      },
    },
  ],
}))

mcp.setRequestHandler(CallToolRequestSchema, async req => {
  const args = (req.params.arguments ?? {}) as Record<string, unknown>
  if (req.params.name !== 'reply') {
    return { content: [{ type: 'text', text: `unknown: ${req.params.name}` }], isError: true }
  }
  const comment_id = args.comment_id as string
  const text = args.text as string
  if (!comment_id || !text) {
    return { content: [{ type: 'text', text: 'reply: comment_id and text required' }], isError: true }
  }
  try {
    await fetch(`${DAEMON_URL}/reply`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ comment_id, text }),
    })
  } catch {
    return { content: [{ type: 'text', text: `reply: daemon not reachable` }], isError: true }
  }
  return { content: [{ type: 'text', text: `replied to ${comment_id}` }] }
})

await mcp.connect(new StdioServerTransport())

const status = await getDaemonStatus()
if (status) {
  process.stderr.write(`review-canvas bridge connected: daemon has ${status.turns} turns from ${status.path || '(none)'}\n`)
}

const LOG_FILE = '/tmp/review-canvas-bridge.log'
function log(msg: string) {
  const line = `[${new Date().toISOString()}] ${msg}\n`
  process.stderr.write(line)
  try { appendFileSync(LOG_FILE, line) } catch {}
}

// Startup marker — confirms updated code is running
log(`bridge pid=${process.pid} starting`)

// Kill any stale bridge process from a previous session
const PID_FILE = '/tmp/review-canvas-bridge.pid'
try {
  const oldPid = parseInt(readFileSync(PID_FILE, 'utf8').trim(), 10)
  if (oldPid && oldPid !== process.pid) {
    try { process.kill(oldPid, 'SIGTERM'); log(`killed stale bridge pid=${oldPid}`) } catch {}
  }
} catch {}
writeFileSync(PID_FILE, String(process.pid))

let bridgeWs: WebSocket | null = null

function connectBridge() {
  bridgeWs = new WebSocket(BRIDGE_WS_URL)
  bridgeWs.onopen = () => log(`ws open`)
  bridgeWs.onerror = (e: Event) => log(`ws error: ${e}`)
  bridgeWs.onmessage = (e: MessageEvent) => {
    const msg = JSON.parse(e.data as string)
    log(`ws msg: ${JSON.stringify(msg)}`)
    // 'comment' = a new review thread; 'followup' = the user continuing an
    // existing thread after Claude has already replied. Both surface to the
    // session as channel events keyed by comment_id; Claude answers either
    // via the `reply` tool with that same comment_id.
    if (msg.type === 'comment' || msg.type === 'followup') {
      const isComment = msg.type === 'comment'
      const params = {
        content: isComment ? `${msg.quote}\n---\n${msg.text}` : msg.text,
        meta: {
          comment_id: isComment ? msg.id : msg.comment_id,
          ...(isComment ? {
            turn_idx: String(msg.turn_idx),
            lines: `${msg.line_start}-${msg.line_end}`,
          } : { followup: 'true' }),
          ts: new Date().toISOString(),
        },
      }
      log(`sending notification: ${JSON.stringify(params)}`)
      mcp.notification({
        method: 'notifications/claude/channel',
        params,
      }).then(() => {
        log(`notification sent ok`)
      }).catch((err: unknown) => {
        log(`notification ERROR: ${err}`)
      })
    }
  }
  bridgeWs.onclose = (e: CloseEvent) => {
    log(`ws close: code=${e.code} reason=${e.reason} wasClean=${e.wasClean}`)
    setTimeout(connectBridge, 1000)
  }
}

connectBridge()

let exiting = false
function exit() {
  if (exiting) return
  exiting = true
  try { unlinkSync(PID_FILE) } catch {}
  process.exit(0)
}

// stdin close = MCP stdio gone; bridge dies with MCP session
process.stdin.on('close', exit)
process.on('SIGTERM', exit)
process.on('SIGINT', exit)
