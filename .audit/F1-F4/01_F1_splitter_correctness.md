# Audit: `_split_observation_blocks` Algorithm Correctness (F1)

**Verdict: PASS with one latent CONCERN**

---

## 1. Algorithm Correctness vs. `format_observation`

`format_observation` (core/prompts.py:93-103) emits:

```
## [2026-05-14T10:00:00] obs_type\n
\n
<body text>\n
```

The final `parts.append("")` at line 103 means every observation ends with a trailing newline after the body. When multiple observations are concatenated into an ephemeral file, the result is:

```
## [T1] type\n\nbody1\n## [T2] type\n\nbody2\n
```

`_split_observation_blocks` (core/consolidator.py:1443-1475) triggers a flush on `stripped.startswith('## ')` (line 1458). On encountering `## [T2]`, it appends `'\n'.join(current).strip()` from `current` (which holds the body lines of block 1), then starts a new `current = [line.rstrip()]`. The `strip()` on the joined block removes the trailing blank line cleanly.

This is an exact match to the format contract. The header line itself is the first element of each returned block, so blocks look like `## [T1] type\n\nbody1` — ready for downstream use. Correctness: **confirmed**.

`format_observation` has been in its current `## [timestamp]` shape since at least the initial commit (ca4f97a), confirmed by `git show 26421dd:core/prompts.py`. The post-F1 splitter was introduced as a working-tree change (not yet a commit) replacing the blank-line splitter added in 56c3f4f.

---

## 2. Edge Cases

### Empty content
`content.splitlines()` returns `[]`; the loop body never executes; `blocks` remains `[]`. The final filter at line 1475 returns `[]`. Safe — callers at lines 357-359 handle empty lists correctly.

### `## ` inside body text (markdown subheading)
**This is the latent CONCERN.** `format_observation` allows free-form `text` (core/prompts.py:97: `parts.append(text.strip())`). If a user or LLM writes an observation body containing a line like `## Subheading`, the splitter at line 1458-1464 will treat it as a new block boundary, splitting the observation in two. The body fragment before the subheading becomes one block; the subheading and subsequent content become another. The ordinal count for that file increases by one without a matching `_record_observations` row — causing a silent index skew.

Mitigation requires anchoring the header pattern to the timestamp format: `re.match(r'^## \[\d{4}-\d{2}-\d{2}T', line)`. The current check (`stripped.startswith('## ')`) is necessary but not sufficient.

### Trailing whitespace, blank lines, multiple consecutive headers
- Trailing whitespace: `line.rstrip()` at line 1470 strips trailing spaces; `strip()` at line 1461 strips the final blank line of each block. Clean.
- Blank lines within body: appended verbatim via line 1470. Preserved correctly.
- Multiple consecutive `## ` headers: each flush `current` before it has real body content. The first header produces a block containing only the header line; it passes the `if current` guard (line 1460) and gets emitted. After filtering at line 1475, single-header-only blocks survive if non-empty — they will produce a block with just the header string. That is technically correct (empty-body observation is valid) but may surprise callers.

### Legacy ephemeral files pre-dating `## ` convention
`git log -S "format_observation"` and inspection of the initial commit confirm `format_observation` has used `## [timestamp]` headers since the very first commit (ca4f97a). There are no pre-convention legacy files in the codebase. The `---` divider path at lines 1465-1469 exists as a safety net for hypothetical external injections, not for real legacy data.

### `---` divider behavior
The `---` pre-header flush (lines 1465-1469) fires only when `not saw_header`. Once any `## ` header has been seen, subsequent `---` lines are treated as body content (line 1470). This is correct for the format: `---` separators only appear as YAML front-matter in old-style files, never inside observation bodies produced by `format_observation`.

---

## 3. Consistency: `_record_observations` vs. `_inject_observation_ids`

`_record_observations` (core/consolidator.py:349-385) calls `_split_observation_blocks(filtered_content)` (line 358) and assigns `ordinal=index` (0-indexed, line 369), exposing `ordinal: index + 1` (1-indexed) in the returned `refs` list (line 380).

`_inject_observation_ids` (core/consolidator.py:399-415) calls `_split_observation_blocks(content)` (line 406) on the same `filtered_content` (line 140), then assigns `ordinal = i + 1` (1-indexed, line 412).

Both call the same function on the same string, so they produce identical `N` blocks in identical order — **unless** `sort_by_salience` (core/replay.py:82-105) has been applied. The pipeline at lines 118-140 of consolidator.py shows:

1. `filtered_content = sort_by_salience(filtered_content)` (line 118)
2. `observation_refs = _record_observations(raw_content, filtered_content, ...)` (line 119-124)
3. `numbered_content = _inject_observation_ids(filtered_content, observation_refs)` (line 140)

Both `_record_observations` and `_inject_observation_ids` receive the **post-sort** `filtered_content`, so ordinal alignment is preserved. **No skew.** However: `replay.py` uses a different regex (`_OBS_SPLIT_RE = re.compile(r"(?=^##\s+\[)", re.MULTILINE)`, line 32) that uses a lookahead to split while keeping delimiters, then reassembles with `"".join(parts)` (line 104) — the header is the first character of each block text, so the output string is structurally identical to the input and `_split_observation_blocks` will parse it the same way. Alignment confirmed.

---

## 4. Regression Risk

The only guard between a `format_observation` format change and a silent splitter break is the docstring at line 1444. If `format_observation` ever switches from `## [timestamp]` to, say, `### ` or `**timestamp**` headers, `_split_observation_blocks` will stop splitting entirely, returning the whole file as one block. No test currently asserts that block count equals observation count in a multi-observation file.

---

## 5. Recommended Unit Tests (file:line citations only)

1. **Round-trip split count:** Construct N calls to `format_observation` (core/prompts.py:79), concatenate output, call `_split_observation_blocks` (core/consolidator.py:1443), assert `len(result) == N`. Locks the header-delimiter contract. See existing test patterns at tests/test_prompts.py and tests/test_schemas.py for fixture style.

2. **Body-embedded `## ` subheading isolation:** Pass a single `format_observation` result whose `text` argument contains a `## Subheading` line; assert `len(_split_observation_blocks(result)) == 1` (currently fails — documents the CONCERN above and gates a fix).

3. **Ordinal alignment between `_record_observations` and `_inject_observation_ids`:** Given a two-observation ephemeral string, assert that the ordinals in the refs returned by `_record_observations` match the `OBSERVATION_ID: N` prefixes injected by `_inject_observation_ids`. See core/consolidator.py:349 and 399 for the two functions to exercise together.
