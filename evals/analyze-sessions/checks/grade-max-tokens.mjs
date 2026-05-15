#!/usr/bin/env node
/**
 * Grader: max-tokens-truncation case
 *
 * The agent should have:
 * 1. Found parse_error_repaired in the trace
 * 2. Identified max_tokens cap as root cause
 * 3. Noted Stage 1 ran but produced 0 obs
 * 4. Proposed the max_tokens bump (Tier 0 Bug 4 / 8K→16K cap increase)
 * 5. NOT misattributed the miss to cwd bug or prefilter
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
    name: 'found-parse-error-repaired',
    pattern: /parse.?error.?repaired/,
    required: true,
    desc: 'Agent found parse_error_repaired trace event'
  },
  {
    name: 'identified-truncation',
    pattern: /\b(truncat|max.?token|token.?limit|8191|8k.?cap|token.?cap)\b/,
    required: true,
    desc: 'Agent identified max_tokens/truncation as root cause'
  },
  {
    name: 'stage1-ran',
    pattern: /stage.?1.{0,40}(ran|did run|processed|extract)/,
    required: false,
    desc: 'Agent noted Stage 1 ran (not a discovery miss)'
  },
  {
    name: 'zero-obs-from-truncation',
    pattern: /\b(0 obs|zero obs|no obs|nothing extracted).{0,200}(truncat|parse|token)/,
    required: false,
    desc: 'Agent linked truncation → 0 observations'
  },
  {
    name: 'fix-proposed',
    pattern: /\b(max.?token.{0,30}(bump|increas|raise|fix)|tier.?0|bug.?4|8k.{0,10}16k|16k.{0,10}8k|token.{0,20}cap.{0,20}(fix|increas))\b/,
    required: true,
    desc: 'Agent proposed max_tokens increase fix'
  },
  {
    name: 'no-false-cwd-attribution',
    // Should NOT say cwd is the problem — cwd is correctly present in this case
    pattern: /\b(cwd.{0,30}(null|missing|bug|cause|issue)|session.?type.?unknown.{0,30}cause)\b/,
    invert: true,
    required: false,
    desc: 'Agent did not falsely attribute to cwd bug'
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
