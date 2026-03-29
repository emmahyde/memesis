# Panel Wave 2: Debate and Convergence — Thread Retrieval

## Kai (Revised)
- Agrees with Dev: batch query is the right fix, not FTS in injection path
- Agrees with Mira: add last_surfaced_at (timestamp only, no scoring weight yet)
- Disagrees with Mira: no get_threads_for_context(), no stored relevance_score, no independent thread injection
- Proposes: batch query + separate THREAD_BUDGET_CHARS (~16K) + optional FTS in active_search only
- Decision points: thread budget policy, thread injection when members archived, FTS scope

## Mira (Revised)
- Agrees with Dev: batch query fixes N+1
- Agrees with Kai: thread budget is needed (~16K chars)
- Drops: independent thread scoring, stored relevance_score column
- Keeps: last_surfaced_at column
- Concedes: FTS on threads scoped to active_search only, not injection
- Proposes: batch query + separate budget + shortest-narrative-first sort + last_surfaced_at
- Decision points: sort order for budget selection, thread narratives in active_search, narrative length cap

## Dev (Revised)
- Agrees: batch query is correct fix
- Agrees: last_surfaced_at worth adding (lazy update)
- Disagrees with Kai: budget should be subordinate to Tier-2, not additive
- Disagrees with Mira: no stored relevance_score, no independent scoring
- Proposes: batch query + 800-char per-narrative truncation (no separate budget) + last_surfaced_at
- Decision points: budget accounting (subordinate vs separate), membership-based vs independent injection
