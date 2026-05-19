#!/usr/bin/env bash
set -euo pipefail

WORKTREE_NAME="${1:?Usage: teardown-worktree.sh <branch-name>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKTREE_DIR="$REPO_ROOT/.claude/worktrees/$WORKTREE_NAME"

if [[ ! -d "$WORKTREE_DIR" ]]; then
    echo "Worktree not found: $WORKTREE_DIR"
    exit 1
fi

echo "Removing worktree '$WORKTREE_NAME'..."
rtk git worktree remove "$WORKTREE_DIR" --force
echo "Done."
