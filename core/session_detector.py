"""
Session type detection heuristics for the memesis ingest pipeline.

Sprint B WS-G / LLME-F9 / OD-B.

No LLM calls. Heuristic only: cwd path hints first, tool-mix second,
default fallback last.

SESSION_TYPE_VALUES mirrors core/validators.py — None is a valid
intermediate value (means "ambiguous") but detect_session_type()
always returns a concrete string.
"""

from __future__ import annotations

SESSION_TYPE_VALUES: frozenset[str | None] = frozenset({"code", "writing", "research", None})

# Path substring hints — checked case-insensitively.
# Specific over broad: a generic "/projects/" hint mis-classifies anything
# under ~/projects/ as code (e.g. memesis observer sessions, research notes
# living in a projects subdir). Pin to known code-project subpaths instead.
CODE_PATH_HINTS: tuple[str, ...] = (
    "/projects/sector",
    "/projects/ccmanager",
    "/projects/godot",
    "/projects/claude-mem",
    "/repos/",
    "/sector/",
    "/code/",
    "/src/",
    "/dev/",
)

# Memesis observer/agent sessions live under ~/.claude-mem/ or
# /projects/memesis/ and are research-flavored, not code-flavored.
RESEARCH_PATH_HINTS_PREPEND: tuple[str, ...] = (
    "/.claude-mem",
    "/projects/memesis",
    "/observer-ses",
)

WRITING_PATH_HINTS: tuple[str, ...] = (
    "/manuscript",
    "/chapter",
    "/prose",
    "/draft",
    "/novel",
    "/writing",
    "/fiction",
    "/story",
)

RESEARCH_PATH_HINTS: tuple[str, ...] = RESEARCH_PATH_HINTS_PREPEND + (
    "/research",
    "/papers",
    "/external_references",
    "/notes",
    "/references",
    "/literature",
)

# File extension sets for tool-mix heuristic
_CODE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".cs", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java",
    ".cpp", ".c", ".h", ".rb", ".swift", ".kt", ".scala", ".ex", ".exs",
    ".sh", ".bash", ".zsh", ".fish", ".ps1",
})

_PROSE_EXTENSIONS: frozenset[str] = frozenset({
    ".txt", ".rtf", ".doc", ".docx", ".odt",
    # .md is ambiguous — could be docs in a code project; handled in tool mix logic
})

_TOOL_CODE_NAMES: frozenset[str] = frozenset({"Edit", "Write", "Bash", "MultiEdit"})
_TOOL_RESEARCH_NAMES: frozenset[str] = frozenset({"WebFetch", "WebSearch", "Read"})


def detect_session_type_from_cwd(cwd: str | None) -> str | None:
    """Detect session type from current working directory path.

    Returns 'code', 'writing', 'research', or None if ambiguous.
    Checks are ordered: writing/research are specific overrides;
    code hints are broad and act as a weak catch-all (returning None
    so that a subsequent tool-mix check can confirm).
    """
    if not cwd:
        return None

    lower = cwd.lower()

    # Writing and research hints are specific — check first
    for hint in WRITING_PATH_HINTS:
        if hint in lower:
            return "writing"

    for hint in RESEARCH_PATH_HINTS:
        if hint in lower:
            return "research"

    # Code hints are broad; confirm with tool-mix if possible — return code
    # only from path if the hint is strong (contains /projects/, /repos/, etc.)
    # but NOT a subdirectory that implies writing/research (already excluded above).
    for hint in CODE_PATH_HINTS:
        if hint in lower:
            return "code"

    return None


def detect_session_type_from_tools(tool_uses: list[dict]) -> str | None:
    """Detect session type from tool usage patterns.

    Heuristic:
    - code: predominantly Edit/Write/Bash on code file extensions
    - research: predominantly Read on .md files + WebFetch/WebSearch present
    - writing: predominantly Edit/Write on prose extensions (.txt, .rtf, .doc etc.)
    - None: ambiguous or insufficient signal

    Args:
        tool_uses: List of dicts with at least 'tool_name' key;
                   optionally 'file_path' or 'path' for extension inspection.

    Returns:
        'code', 'writing', 'research', or None.
    """
    if not tool_uses:
        return None

    tool_names: list[str] = [t.get("tool_name", "") for t in tool_uses]

    code_tool_count = sum(1 for n in tool_names if n in _TOOL_CODE_NAMES)
    research_tool_count = sum(
        1 for n in tool_names if n in {"WebFetch", "WebSearch"}
    )
    read_count = sum(1 for n in tool_names if n == "Read")

    # Collect file paths for extension inspection
    file_paths: list[str] = []
    for t in tool_uses:
        path = t.get("file_path") or t.get("path") or ""
        if path:
            file_paths.append(path.lower())

    code_file_count = sum(
        1 for p in file_paths if any(p.endswith(ext) for ext in _CODE_EXTENSIONS)
    )
    prose_file_count = sum(
        1 for p in file_paths if any(p.endswith(ext) for ext in _PROSE_EXTENSIONS)
    )
    md_read_count = sum(
        1 for t in tool_uses
        if t.get("tool_name") == "Read"
        and (t.get("file_path", "") or t.get("path", "")).endswith(".md")
    )

    total = len(tool_uses)
    if total == 0:
        return None

    # Research signal: has web tools AND Read on .md files
    if research_tool_count >= 1 and md_read_count >= 1:
        return "research"

    # Dominant research without web: many .md reads, no code edits
    if md_read_count >= 2 and code_tool_count == 0 and code_file_count == 0:
        return "research"

    # Writing signal: Edit/Write on prose extensions dominate
    if prose_file_count >= 1 and code_file_count == 0 and research_tool_count == 0:
        return "writing"

    # Code signal: Edit/Write/Bash with code extensions, or just heavy tool usage
    if code_file_count >= 1:
        return "code"

    if code_tool_count >= 2 and prose_file_count == 0:
        return "code"

    return None


def detect_session_type(
    cwd: str | None,
    tool_uses: list[dict] | None = None,
    default: str = "code",
) -> str:
    """Combine cwd hint + tool-mix + default to always return a session_type.

    Priority order:
    1. cwd path hint (most reliable — explicit project paths)
    2. Tool-mix heuristic (good secondary signal)
    3. default (fallback; 'code' because memesis is software-first)

    Args:
        cwd: Current working directory path, or None.
        tool_uses: List of tool use dicts from the transcript slice, or None.
        default: Fallback session type. Should be one of SESSION_TYPE_VALUES.

    Returns:
        One of 'code', 'writing', 'research'.
    """
    cwd_type = detect_session_type_from_cwd(cwd)
    if cwd_type is not None:
        return cwd_type

    if tool_uses:
        tool_type = detect_session_type_from_tools(tool_uses)
        if tool_type is not None:
            return tool_type

    return default
