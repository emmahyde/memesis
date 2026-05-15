#!/usr/bin/env bash
set -euo pipefail

# Setup a git worktree for memesis development.
# Usage: scripts/setup-worktree.sh <branch-name> [base-branch]
#
# Creates .claude/worktrees/<branch-name>/, sets up Python env, installs deps.

WORKTREE_NAME="${1:?Usage: setup-worktree.sh <branch-name> [base-branch]}"
BASE_BRANCH="${2:-main}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKTREE_DIR="$REPO_ROOT/.claude/worktrees/$WORKTREE_NAME"

if [[ -d "$WORKTREE_DIR" ]]; then
    echo "Worktree already exists: $WORKTREE_DIR"
    exit 1
fi

echo "Creating worktree '$WORKTREE_NAME' from '$BASE_BRANCH'..."
mkdir -p "$REPO_ROOT/.claude/worktrees"
rtk git worktree add "$WORKTREE_DIR" -b "$WORKTREE_NAME" "$BASE_BRANCH"

echo "Setting up Python environment..."
cd "$WORKTREE_DIR"

if command -v uv >/dev/null 2>&1; then
    uv venv .venv
    uv pip install -e ".[dev]"
else
    python3 -m venv .venv
    .venv/bin/pip install -e ".[dev]"
fi

echo "Done. Worktree ready at: $WORKTREE_DIR"
echo "Activate with: cd $WORKTREE_DIR && source .venv/bin/activate"
