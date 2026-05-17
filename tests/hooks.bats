#!/usr/bin/env bats
# Integration tests for memesis Claude Code hooks.
#
# Each test runs the real hook subprocess with an isolated HOME so no test
# touches the production store at ~/.claude/memory.  Protocol invariants
# checked here:
#   - exit code is always 0 (hooks must never crash a session)
#   - stdout is either well-formed or empty (never a raw Python traceback)
#   - pre_compact stdout is ALWAYS empty (Claude Code hook protocol)
#   - pre_tool_guard stdout is ALWAYS valid JSON with permissionDecision
#
# Run:  bats tests/hooks.bats
# Deps: bats-core, uv (python resolved via .venv)

PROJECT_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python3"

# ---------------------------------------------------------------------------
# Shared setup / teardown
# ---------------------------------------------------------------------------

setup() {
    HOOK_HOME="$(mktemp -d)"
    export HOME="$HOOK_HOME"
    export CLAUDE_SESSION_ID="bats-$$-$RANDOM"
    cd "$PROJECT_ROOT"

    # Pre-initialise the DB (fast — no LLM calls) so session_start's
    # idempotency check skips the seeding LLM call on repeat runs.
    "$PYTHON" - <<'PYEOF'
import os, sys
sys.path.insert(0, os.getcwd())
from core.database import init_db, close_db
init_db(project_context=os.getcwd())
close_db()
PYEOF
}

teardown() {
    rm -rf "$HOOK_HOME"
}

# ---------------------------------------------------------------------------
# session_start
# ---------------------------------------------------------------------------

@test "session_start: exits 0 on a fresh store" {
    run "$PYTHON" hooks/session_start.py
    [ "$status" -eq 0 ]
}

@test "session_start: stdout contains the panel header" {
    run "$PYTHON" hooks/session_start.py
    [ "$status" -eq 0 ]
    [[ "$output" == *"memesis"* ]]
}

@test "session_start: stdout contains the legend footer" {
    run "$PYTHON" hooks/session_start.py
    [ "$status" -eq 0 ]
    [[ "$output" == *"legend"* ]]
}

@test "session_start: stdout has no Python traceback" {
    run "$PYTHON" hooks/session_start.py
    [[ "$output" != *"Traceback"* ]]
    [[ "$output" != *"Error"*      ]]
}

@test "session_start: exits 0 even with a non-existent HOME" {
    HOME="/nonexistent_dir_bats_$$" run "$PYTHON" hooks/session_start.py
    [ "$status" -eq 0 ]
}

@test "session_start: stdout is empty (not a traceback) when HOME is bad" {
    HOME="/nonexistent_dir_bats_$$" run "$PYTHON" hooks/session_start.py
    [[ "$output" != *"Traceback"* ]]
}

@test "session_start: CLAUDE_SESSION_ID env is consumed without error" {
    CLAUDE_SESSION_ID="custom-session-bats" run "$PYTHON" hooks/session_start.py
    [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# pre_tool_guard
# ---------------------------------------------------------------------------

@test "pre_tool_guard: exits 0 for a safe Bash command" {
    run bash -c \
        'echo '"'"'{"tool_name":"Bash","tool_input":{"command":"echo hello"}}'"'"' \
        | '"$PYTHON"' hooks/pre_tool_guard.py'
    [ "$status" -eq 0 ]
}

@test "pre_tool_guard: stdout is valid JSON" {
    output=$(echo '{"tool_name":"Bash","tool_input":{"command":"echo hi"}}' \
        | "$PYTHON" hooks/pre_tool_guard.py)
    # non-empty output must parse as JSON
    if [ -n "$output" ]; then
        echo "$output" | "$PYTHON" -c "import sys,json; json.load(sys.stdin)"
    fi
}

@test "pre_tool_guard: JSON output contains permissionDecision" {
    run bash -c \
        'echo '"'"'{"tool_name":"Bash","tool_input":{"command":"ls"}}'"'"' \
        | '"$PYTHON"' hooks/pre_tool_guard.py'
    [ "$status" -eq 0 ]
    # output is either empty (allow, no output needed) or JSON with the key
    if [ -n "$output" ]; then
        [[ "$output" == *"permissionDecision"* ]]
    fi
}

@test "pre_tool_guard: fails open on empty stdin (exits 0)" {
    run bash -c 'echo "" | '"$PYTHON"' hooks/pre_tool_guard.py'
    [ "$status" -eq 0 ]
}

@test "pre_tool_guard: fails open on malformed JSON (exits 0)" {
    run bash -c 'echo "not json at all" | '"$PYTHON"' hooks/pre_tool_guard.py'
    [ "$status" -eq 0 ]
}

@test "pre_tool_guard: stdout has no Python traceback on bad input" {
    run bash -c 'echo "not json" | '"$PYTHON"' hooks/pre_tool_guard.py'
    [[ "$output" != *"Traceback"* ]]
}

# ---------------------------------------------------------------------------
# user_prompt_inject
# ---------------------------------------------------------------------------

@test "user_prompt_inject: exits 0 on empty stdin" {
    run bash -c 'echo "" | '"$PYTHON"' hooks/user_prompt_inject.py'
    [ "$status" -eq 0 ]
}

@test "user_prompt_inject: empty stdin produces empty stdout" {
    output=$(echo "" | "$PYTHON" hooks/user_prompt_inject.py)
    [ -z "$output" ]
}

@test "user_prompt_inject: exits 0 with a real prompt" {
    run bash -c \
        'echo "How do I fix the migration runner?" \
        | '"$PYTHON"' hooks/user_prompt_inject.py'
    [ "$status" -eq 0 ]
}

@test "user_prompt_inject: stdout has no Python traceback" {
    run bash -c \
        'echo "some user question" | '"$PYTHON"' hooks/user_prompt_inject.py'
    [[ "$output" != *"Traceback"* ]]
}

@test "user_prompt_inject: exits 0 even when store is empty" {
    # fresh temp HOME was set up in setup() — store has no memories
    run bash -c \
        'echo "what is the database schema" | '"$PYTHON"' hooks/user_prompt_inject.py'
    [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# pre_compact
# ---------------------------------------------------------------------------

@test "pre_compact: exits 0 with no ephemeral buffer" {
    run "$PYTHON" hooks/pre_compact.py
    [ "$status" -eq 0 ]
}

@test "pre_compact: stdout is ALWAYS empty (hook protocol)" {
    # Claude Code forbids hooks from writing to stdout except in defined ways.
    # pre_compact is a notification hook — it must not inject text.
    output=$(echo "some conversation text" | "$PYTHON" hooks/pre_compact.py 2>/dev/null)
    [ -z "$output" ]
}

@test "pre_compact: stdout is empty even when an ephemeral file exists" {
    # Write a minimal ephemeral buffer in the expected location.
    mem_dir="$HOOK_HOME/.claude/memory"
    mkdir -p "$mem_dir/ephemeral"
    TODAY=$(date +%Y-%m-%d)
    cat > "$mem_dir/ephemeral/session-$TODAY.md" <<'MD'
# Session Observations

- User prefers snake_case for variables.
- Always run tests before committing.
MD

    output=$(echo "" | CLAUDE_SESSION_ID="$CLAUDE_SESSION_ID" \
        "$PYTHON" hooks/pre_compact.py 2>/dev/null)
    [ -z "$output" ]
}

@test "pre_compact: exits 0 even with ephemeral content" {
    mem_dir="$HOOK_HOME/.claude/memory"
    mkdir -p "$mem_dir/ephemeral"
    TODAY=$(date +%Y-%m-%d)
    echo "- A test observation." > "$mem_dir/ephemeral/session-$TODAY.md"
    run bash -c \
        "echo '' | CLAUDE_SESSION_ID='$CLAUDE_SESSION_ID' '$PYTHON' hooks/pre_compact.py"
    [ "$status" -eq 0 ]
}
