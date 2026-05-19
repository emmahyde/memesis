#!/usr/bin/env node
/**
 * Setup: good-session-control case
 *
 * Session ses_good001: one clear correction, cwd correctly present.
 * Stage 1 ran, extracted 1 observation, appended to ephemeral.
 * Grader checks agent does NOT raise false alarms.
 */

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import os from 'os';

const WORK = process.env.WORK || '/work';
const CLAUDE_DIR = path.join(WORK, '.claude');
const SESSION_ID = 'ses_good001';

const transcriptDir = path.join(CLAUDE_DIR, 'transcripts');
fs.mkdirSync(transcriptDir, { recursive: true });

const transcript = [
  {
    type: 'user',
    cwd: '/Users/emmahyde/projects/memesis',
    message: { content: 'Add a helper to format duration in ms to a human string.' }
  },
  {
    type: 'assistant',
    message: {
      content: [
        { type: 'text', text: 'Adding format_duration() utility.' },
        { type: 'tool_use', name: 'Edit', id: 't1',
          input: { file_path: 'core/utils.py',
            old_string: '# utils', new_string: 'def format_duration(ms): ...' } }
      ]
    }
  },
  {
    type: 'user',
    message: { content: 'No — don\'t use utils.py, put it in core/trace.py next to the other timing helpers.' }
  },
  {
    type: 'assistant',
    message: {
      content: [{ type: 'text', text: 'Moving to core/trace.py.' }]
    }
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
  ('${SESSION_ID}','${transcriptPath}',512,1747186000,1747187200,'/Users/emmahyde/projects/memesis'))
conn.commit(); conn.close()
"`);

const tracesDir = path.join(memesisDir, 'traces');
fs.mkdirSync(tracesDir, { recursive: true });

const trace = [
  {
    ts: '2026-05-14T13:00:00Z',
    session_id: SESSION_ID,
    event_type: 'stage1_extract_start',
    data: { rendered_chars: 512, session_type: 'code', flow: 'simple' }
  },
  {
    ts: '2026-05-14T13:00:04Z',
    session_id: SESSION_ID,
    event_type: 'stage1_append_ephemeral',
    data: {
      n_appended: 1,
      n_lines_emitted: 1,
      target: path.join(CLAUDE_DIR, 'projects',
        '-Users-emmahyde-projects-memesis', 'memory', 'ephemeral', 'session-2026-05-14.md')
    }
  },
  {
    ts: '2026-05-14T13:00:04Z',
    session_id: SESSION_ID,
    event_type: 'stage1_extract_end',
    data: { n_obs: 1, n_dropped: 0, outcome: 'success', session_type: 'code' }
  },
];

fs.writeFileSync(
  path.join(tracesDir, `${SESSION_ID}.jsonl`),
  trace.map(e => JSON.stringify(e)).join('\n') + '\n'
);

// Ephemeral buffer with the captured observation
const ephemeralDir = path.join(CLAUDE_DIR, 'projects',
  '-Users-emmahyde-projects-memesis', 'memory', 'ephemeral');
fs.mkdirSync(ephemeralDir, { recursive: true });

fs.writeFileSync(
  path.join(ephemeralDir, 'session-2026-05-14.md'),
  `## [correction | 2026-05-14]
Timing/duration helpers belong in core/trace.py, not core/utils.py. User corrected placement during format_duration helper addition.
`
);

fs.writeFileSync(path.join(WORK, '.memesis-eval-scenario'), 'good-session\n');

console.log(`Setup complete: ${SESSION_ID} — good-session scenario`);
