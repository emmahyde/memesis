#!/usr/bin/env node
/**
 * Setup: cwd-bug-miss case
 *
 * Session ses_cwd001 was ingested but cwd=NULL in cursors.db (the known #5 blocker).
 * Stage 1 ran but classified session as session_type=unknown → extraction yielded 0 obs.
 * The transcript has 2 clear corrections + 1 decision the pipeline should have caught.
 */

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import os from 'os';

// All fake data goes under /work/.claude so it's accessible to the agent container.
// Setup and agent run in separate containers; only /work is a shared volume.
const WORK = process.env.WORK || '/work';
const CLAUDE_DIR = path.join(WORK, '.claude');
const SESSION_ID = 'ses_cwd001';

// --- Transcript (no cwd on user/assistant entries — simulates the #5 bug) ---
const transcriptDir = path.join(CLAUDE_DIR, 'transcripts');
fs.mkdirSync(transcriptDir, { recursive: true });

const transcript = [
  // Attachment entry carries cwd — but read_transcript_from() filters these out
  { type: 'attachment', cwd: '/Users/emmahyde/projects/memesis', message: {} },
  {
    type: 'user',
    message: {
      content: 'I want to add a new field to the Memory model for tracking confidence scores.'
    }
  },
  {
    type: 'assistant',
    message: {
      content: [{ type: 'text', text: 'I can add a `confidence` FloatField to the Memory model. Here\'s my approach...' }]
    }
  },
  {
    type: 'user',
    message: {
      content: 'No, don\'t add it to Memory directly. Add it to ConsolidationLog instead — that\'s where per-decision confidence belongs.'
    }
  },
  {
    type: 'assistant',
    message: {
      content: [{ type: 'text', text: 'Understood, adding to ConsolidationLog instead.' }]
    }
  },
  {
    type: 'user',
    message: {
      content: 'Actually stop — we decided last week to track confidence in the issue cards schema, not in ConsolidationLog. Use IssueCard.'
    }
  },
  {
    type: 'assistant',
    message: {
      content: [{ type: 'text', text: 'Got it. I\'ll put confidence on IssueCard.' }]
    }
  },
  {
    type: 'user',
    message: {
      content: 'OK good. And going forward, always check .context/DESIGN_ETHOS.md before adding new fields to core models.'
    }
  },
];

fs.writeFileSync(
  path.join(transcriptDir, `${SESSION_ID}.jsonl`),
  transcript.map(e => JSON.stringify(e)).join('\n') + '\n'
);

// --- Cursors DB (session present but cwd=NULL — the bug) ---
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
  ('${SESSION_ID}','${transcriptPath}',847,1747180000,1747183600,None))
conn.commit(); conn.close()
"`);

// --- Trace JSONL (Stage 1 ran but produced 0 obs due to session_type=unknown) ---
const tracesDir = path.join(memesisDir, 'traces');
fs.mkdirSync(tracesDir, { recursive: true });

const trace = [
  {
    ts: '2026-05-14T10:00:00Z',
    session_id: SESSION_ID,
    event_type: 'stage1_extract_start',
    data: { rendered_chars: 847, session_type: 'unknown', flow: 'simple' }
  },
  {
    ts: '2026-05-14T10:00:03Z',
    session_id: SESSION_ID,
    event_type: 'stage1_extract_end',
    data: { n_obs: 0, n_dropped: 0, outcome: 'empty_or_skipped', session_type: 'unknown' }
  },
];

fs.writeFileSync(
  path.join(tracesDir, `${SESSION_ID}.jsonl`),
  trace.map(e => JSON.stringify(e)).join('\n') + '\n'
);

// --- Ephemeral buffer (nothing written for this session) ---
const ephemeralDir = path.join(CLAUDE_DIR, 'projects',
  '-Users-emmahyde-projects-memesis', 'memory', 'ephemeral');
fs.mkdirSync(ephemeralDir, { recursive: true });

// --- Scenario marker for uv mock ---
fs.writeFileSync(path.join(WORK, '.memesis-eval-scenario'), 'cwd-bug\n');

console.log(`Setup complete: ${SESSION_ID} — cwd-bug scenario`);
