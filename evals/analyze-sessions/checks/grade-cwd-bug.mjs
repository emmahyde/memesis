#!/usr/bin/env node
/**
 * Grader: cwd-bug-miss case
 *
 * The agent should have:
 * 1. Found 0 observations extracted
 * 2. Identified cwd=NULL in cursors.db as the root cause
 * 3. Linked it to session_type=unknown defeating extraction
 * 4. Proposed the cwd extraction fix (#5 / Tier 1 or Tier 0 blocker)
 * 5. NOT reported the session as "never processed" (cursor row exists)
 */

import fs from 'fs';
import path from 'path';

const WORK = process.env.WORK || '/work';
const analysisPath = path.join(WORK, 'analysis.md');

let report;
try {
  report = fs.readFileSync(analysisPath, 'utf8').toLowerCase();
} catch {
  console.log(JSON.stringify({
    pass: false, score: 0,
    evidence: ['analysis.md not found — agent did not write output']
  }));
  process.exit(1);
}

const checks = [
  {
    name: 'found-zero-observations',
    pattern: /\b(0 obs|zero obs|no obs|n_obs.*0|0 observation|zero observation|nothing extracted|empty_or_skipped|produced no mem|no mem)\b/,
    required: true,
    desc: 'Agent noted zero observations extracted'
  },
  {
    name: 'cwd-null-identified',
    pattern: /\bcwd\b.{0,60}(null|missing|none|absent|\bnot present\b|\bno cwd\b)/,
    required: true,
    desc: 'Agent identified cwd=NULL in cursors'
  },
  {
    name: 'session-type-unknown',
    pattern: /session.?type.{0,30}unknown/,
    required: true,
    desc: 'Agent linked cwd=NULL → session_type=unknown'
  },
  {
    name: 'stage1-miss-identified',
    pattern: /\b(stage 1|stage1).{0,60}(miss|zero|0 obs|unknown|fail|no obs)/,
    required: false,
    desc: 'Agent attributed loss to Stage 1'
  },
  {
    name: 'cwd-fix-proposed',
    pattern: /\b(cwd.{0,40}fix|fix.{0,40}cwd|#5|read_transcript_from|cursors?.{0,20}cwd|blocker|tier [01])/,
    required: true,
    desc: 'Agent proposed cwd extraction fix'
  },
  {
    name: 'no-false-cursor-miss',
    // Should NOT conclude cursor row is absent — the row EXISTS (cwd just NULL)
    pattern: /\b(cursor.{0,20}(missing|absent|not found|does not exist)|no cursor row|session never seen|not in cursor.{0,20}db)\b/,
    invert: true,
    required: false,
    desc: 'Agent did not incorrectly report session as never processed'
  },
];

const results = checks.map(c => {
  const matched = c.pattern.test(report);
  const pass = c.invert ? !matched : matched;
  return { ...c, pass, matched };
});

const required = results.filter(r => r.required);
const allRequiredPass = required.every(r => r.pass);
const score = results.filter(r => r.pass).length / results.length;

const evidence = results.map(r =>
  `${r.pass ? '✓' : '✗'} [${r.name}] ${r.desc}`
);

console.log(JSON.stringify({
  pass: allRequiredPass,
  score: Math.round(score * 100) / 100,
  evidence
}));
