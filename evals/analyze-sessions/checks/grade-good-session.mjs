#!/usr/bin/env node
/**
 * Grader: good-session-control case
 *
 * The agent should have:
 * 1. Found 1 observation extracted successfully
 * 2. Matched the correction signal (trace.py placement)
 * 3. Reported high/full capture rate — no critical gaps
 * 4. NOT raised false alarms about cwd, truncation, or Stage 1 failure
 * 5. Acknowledged pipeline worked correctly
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
    evidence: ['analysis.md not found']
  }));
  process.exit(1);
}

const checks = [
  {
    name: 'found-observation',
    pattern: /\b(1 obs|one obs|n_obs.*1|n_appended.*1|1 observation|observation.{0,30}captured)\b/,
    required: true,
    desc: 'Agent found 1 observation extracted'
  },
  {
    name: 'correction-signal-matched',
    pattern: /\b(correction|trace\.py|timing.{0,20}helper|utils\.py|placement)\b/,
    required: true,
    desc: 'Agent identified the correction about trace.py placement'
  },
  {
    name: 'reports-ok-or-high-capture',
    pattern: /\b(ok|captured|success|high capture|full capture|100%|1\/1|no gap|no miss|no critical)\b/,
    required: true,
    desc: 'Agent reported OK / high capture rate'
  },
  {
    name: 'no-false-cwd-alarm',
    pattern: /\b(cwd.{0,30}(null|missing|bug|cause)|session.?type.?unknown.{0,30}(cause|problem))\b/,
    invert: true,
    required: true,
    desc: 'Agent did not raise false cwd alarm'
  },
  {
    name: 'no-false-truncation-alarm',
    pattern: /\bparse.?error.?repaired\b/,
    invert: true,
    required: true,
    desc: 'Agent did not raise false truncation alarm'
  },
  {
    name: 'no-critical-gaps',
    pattern: /\b(critical gap|p0|risk-01|missing.{0,30}correction|false negative.{0,30}correction)\b/,
    invert: true,
    required: false,
    desc: 'Agent did not report critical gaps for well-captured session'
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
