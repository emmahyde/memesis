#!/usr/bin/env node
/**
 * Setup: max-tokens-truncation case
 *
 * Session ses_trunc001: dense coding session, cwd correctly present.
 * Stage 1 ran but parse_error_repaired fired → JSON truncated, 0 obs extracted.
 * Transcript has 3 clear signals that should have been captured.
 */

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import os from 'os';

const WORK = process.env.WORK || '/work';
const CLAUDE_DIR = path.join(WORK, '.claude');
const SESSION_ID = 'ses_trunc001';

const transcriptDir = path.join(CLAUDE_DIR, 'transcripts');
fs.mkdirSync(transcriptDir, { recursive: true });

// Build a dense transcript (simulates 16K+ char window)
const longRefactor = 'x'.repeat(3000);
const transcript = [
  {
    type: 'user',
    cwd: '/Users/emmahyde/projects/memesis',
    message: { content: 'Refactor the consolidator to use async/await throughout.' }
  },
  {
    type: 'assistant',
    message: {
      content: [
        { type: 'text', text: 'Refactoring consolidator.py to async. ' + longRefactor },
        { type: 'tool_use', name: 'Edit', id: 't1',
          input: { file_path: 'core/consolidator.py',
            old_string: 'def consolidate', new_string: 'async def consolidate' } }
      ]
    }
  },
  {
    type: 'user',
    message: { content: 'Wait, actually don\'t change the function signatures. Use asyncio.run() at the call sites only.' }
  },
  {
    type: 'assistant',
    message: {
      content: [{ type: 'text', text: 'Reverting signature changes, wrapping calls with asyncio.run() instead.' + longRefactor }]
    }
  },
  {
    type: 'user',
    message: { content: 'Good. And add a note: we chose asyncio.run() over full async refactor because the PreCompact timeout budget is already tight.' }
  },
  {
    type: 'assistant',
    message: {
      content: [{ type: 'text', text: 'Added comment explaining asyncio.run() rationale.' + longRefactor }]
    }
  },
  {
    type: 'user',
    message: { content: 'Perfect. Going forward: never make consolidator function signatures async without a full timeout-budget analysis first.' }
  },
];

fs.writeFileSync(
  path.join(transcriptDir, `${SESSION_ID}.jsonl`),
  transcript.map(e => JSON.stringify(e)).join('\n') + '\n'
);

const memesisDir = path.join(CLAUDE_DIR, 'memesis');
fs.mkdirSync(memesisDir, { recursive: true });

const cursorsDb = path.join(memesisDir, 'cursors.db');
const transcriptPath = path.join(transcriptDir, SESSION_ID + '.jsonl');
execSync(`python3 -c "
import sqlite3
conn = sqlite3.connect('${cursorsDb}')
conn.execute('''CREATE TABLE IF NOT EXISTS transcript_cursors (
  session_id TEXT PRIMARY KEY, transcript_path TEXT NOT NULL,
  last_byte_offset INTEGER NOT NULL DEFAULT 0, first_seen_at INTEGER NOT NULL,
  last_run_at INTEGER NOT NULL, cwd TEXT DEFAULT NULL)''')
conn.execute('INSERT OR REPLACE INTO transcript_cursors VALUES (?,?,?,?,?,?)',
  ('${SESSION_ID}','${transcriptPath}',14220,1747180000,1747184800,'/Users/emmahyde/projects/memesis'))
conn.commit(); conn.close()
"`);

const tracesDir = path.join(memesisDir, 'traces');
fs.mkdirSync(tracesDir, { recursive: true });

const trace = [
  {
    ts: '2026-05-14T11:00:00Z',
    session_id: SESSION_ID,
    event_type: 'stage1_extract_start',
    data: { rendered_chars: 14220, session_type: 'code', flow: 'simple' }
  },
  {
    ts: '2026-05-14T11:00:12Z',
    session_id: SESSION_ID,
    event_type: 'parse_error_repaired',
    data: {
      outcome: 'repaired',
      original_length: 14220,
      truncated_at: 8191,
      repair_strategy: 'truncate_to_last_complete_object'
    }
  },
  {
    ts: '2026-05-14T11:00:12Z',
    session_id: SESSION_ID,
    event_type: 'stage1_extract_end',
    data: { n_obs: 0, n_dropped: 0, outcome: 'parse_error_repaired', session_type: 'code' }
  },
];

fs.writeFileSync(
  path.join(tracesDir, `${SESSION_ID}.jsonl`),
  trace.map(e => JSON.stringify(e)).join('\n') + '\n'
);

const ephemeralDir = path.join(CLAUDE_DIR, 'projects',
  '-Users-emmahyde-projects-memesis', 'memory', 'ephemeral');
fs.mkdirSync(ephemeralDir, { recursive: true });
// No ephemeral observations for this session

fs.writeFileSync(path.join(WORK, '.memesis-eval-scenario'), 'max-tokens\n');

console.log(`Setup complete: ${SESSION_ID} — max-tokens scenario`);
