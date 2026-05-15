#!/usr/bin/env bash
# Run ingest_one over recent transcripts until at least one new crystallization fires.
# Logs everything to /tmp/ingest_until_crystal_<ts>.log.
set -u
TS=$(date +%Y%m%d_%H%M%S)
LOG=/tmp/ingest_until_crystal_${TS}.log
PROJECT_DIR="$HOME/.claude/projects/-Users-emmahyde-projects-memesis"
CURRENT_SESSION="151dabb2-90b7-447c-b7b0-dc7cf0565a40"

echo "log: $LOG"
echo "log: $LOG" >> "$LOG"

# Snapshot session_ids already represented in observations (any status). Skip them
# to avoid re-running transcripts; replay defeats crystallization signal.
PROCESSED=$(uv run python3 -c "
from core.database import init_db, close_db
from core.models import Observation
init_db(base_dir='$PROJECT_DIR/memory')
for r in Observation.select(Observation.session_id).distinct().dicts():
    if r['session_id']: print(r['session_id'])
close_db()
" 2>/dev/null)

n=0
for f in $(ls -t "$PROJECT_DIR"/*.jsonl); do
  base=$(basename "$f" .jsonl)
  if [[ "$base" == "$CURRENT_SESSION" ]]; then
    echo "skip current session: $base" | tee -a "$LOG"
    continue
  fi
  if echo "$PROCESSED" | grep -qx "$base"; then
    echo "skip already-processed: $base" | tee -a "$LOG"
    continue
  fi
  n=$((n+1))
  echo "=== [$n] $base ===" | tee -a "$LOG"
  marker="__INGEST_DONE_${n}__"
  uv run python3 scripts/ingest_one.py "$f" 2>&1 | tee -a "$LOG"
  echo "$marker" >> "$LOG"
  if awk "/^=== \[$n\] /{flag=1} flag" "$LOG" | grep -q "crystallized this run"; then
    echo "STOP — crystallized this run hit on $base" | tee -a "$LOG"
    break
  fi
done

echo "done. transcripts processed: $n" | tee -a "$LOG"
