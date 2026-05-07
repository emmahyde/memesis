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

let bridgeWs: WebSocket | null = null

function connectBridge() {
  bridgeWs = new WebSocket(BRIDGE_WS_URL)
  bridgeWs.onmessage = (e: MessageEvent) => {
    const msg = JSON.parse(e.data as string)
    if (msg.type === 'comment') {
      void mcp.notification({
        method: 'notifications/claude/channel',
        params: {
          content: `${msg.quote}\n---\n${msg.text}`,
          meta: {
            comment_id: msg.id,
            turn_idx: String(msg.turn_idx),
            lines: `${msg.line_start}-${msg.line_end}`,
            ts: new Date().toISOString(),
          },
        },
      })
    }
  }
  bridgeWs.onclose = () => setTimeout(connectBridge, 1000)
}

connectBridge()

let exiting = false
function exit() {
  if (exiting) return
  exiting = true
  process.exit(0)
}

// stdin close = MCP stdio gone; bridge dies with MCP session
process.stdin.on('close', exit)
process.on('SIGTERM', exit)
process.on('SIGINT', exit)
