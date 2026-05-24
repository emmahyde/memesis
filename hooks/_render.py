"""ANSI panel renderer for the SessionStart memory injection.

Pure rendering — no DB access, no I/O. ``render_panel`` takes already-gathered
data and returns the panel string the SessionStart hook emits. Kept dependency-
free (no `rich`) so it imports instantly under the 5s hook timeout.

Panel layout, top (oldest) to bottom (newest):

    ╭─ memesis · headers ─────────────────────────────
    │ 🧭 ROLE / ⚖️ RULES / ⚠️ WATCH        (never trimmed)
    ├─ 📅 <session topic> · <date> ───────────────────
    │   ▸ <topic cluster>
    │       <emoji> <memory title>          (edge-linked: ↳)
    ├─ ⛔ CONFLICTS ──────────────────────────────────  (never trimmed)
    ╰─ legend ────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime

# Emoji per kind — also the legend. Post-#17 single 7+2 vocab.
KIND_EMOJI = {
    "decision": "📌", "fact": "🔹", "lesson": "💡", "correction": "⚠️",
    "directive": "⚖️", "preference": "💬", "goal": "🎯",
    "open_question": "❓", "hypothesis": "🧪",
}
_DEFAULT_EMOJI = "🔸"
_SEVERITY_TAG = {"block": "⛔", "ask": "❓", "warn": "⚠️"}

_WIDTH = 60


def estimate_tokens(text: str) -> int:
    """Rough token estimate — ~4 characters per token."""
    return len(text) // 4


def _get(obj, key, default=None):
    """Read a field from either an object (getattr) or a dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _emoji(kind: str | None) -> str:
    return KIND_EMOJI.get(kind or "", _DEFAULT_EMOJI)


def _rule(label: str, corner: str = "├") -> str:
    """A horizontal divider line carrying a short label."""
    head = f"{corner}─ {label} "
    return head + "─" * max(2, _WIDTH - len(head))


def _date_of(iso: str | None) -> str:
    """Best-effort YYYY-MM-DD from an ISO timestamp."""
    if not iso:
        return "undated"
    return str(iso)[:10]


def _order_by_edges(mems: list, edge_pairs: set) -> tuple[list, set]:
    """Reorder one cluster so edge-connected memories are contiguous.

    Returns ``(ordered, linked_ids)`` — ``linked_ids`` are the non-first
    members of multi-memory connected components, marked with a connector.
    ``mems`` is assumed already in chronological order.
    """
    ids = [m.id for m in mems]
    idset = set(ids)
    adj: dict = {i: set() for i in ids}
    for a, b in edge_pairs:
        if a in idset and b in idset and a != b:
            adj[a].add(b)
            adj[b].add(a)

    seen: set = set()
    ordered: list = []
    linked: set = set()
    for m in mems:
        if m.id in seen:
            continue
        # Collect the whole connected component (BFS).
        component: set = set()
        stack = [m.id]
        while stack:
            cur = stack.pop()
            if cur in component:
                continue
            component.add(cur)
            stack.extend(adj[cur] - component)
        comp_mems = [x for x in mems if x.id in component]
        for idx, cm in enumerate(comp_mems):
            seen.add(cm.id)
            ordered.append(cm)
            if len(comp_mems) > 1 and idx > 0:
                linked.add(cm.id)
    return ordered, linked


class _Mem:
    """Normalised memory record used internally by the renderer."""

    __slots__ = ("id", "title", "kind", "session", "cluster", "created_at", "stage")

    def __init__(self, src):
        self.id = _get(src, "id") or ""
        self.title = (_get(src, "title") or "(untitled)").strip()
        self.kind = _get(src, "kind")
        self.session = _get(src, "source_session") or ""
        self.cluster = _get(src, "cluster")
        self.created_at = _get(src, "created_at") or ""
        self.stage = _get(src, "stage") or "consolidated"


def _render_session_block(session_label: str, clusters: list) -> list[str]:
    """Render one session: a divider plus its topic clusters. clusters is a
    list of (cluster_label, lines) — already laid out, oldest cluster last."""
    lines = [_rule(f"📅 {session_label}")]
    for cluster_label, mem_lines in clusters:
        lines.append(f"│   ▸ {cluster_label}")
        lines.extend(mem_lines)
    return lines


def _build_clusters(mems: list[_Mem], edge_pairs: set) -> list[tuple[str, list[str]]]:
    """Group one session's memories into topic clusters, newest cluster first."""
    by_cluster: dict = {}
    for m in mems:
        by_cluster.setdefault(m.cluster, []).append(m)

    blocks: list[tuple[str, list[str], str]] = []  # (label, lines, recency)
    for cluster_key, cluster_mems in by_cluster.items():
        cluster_mems.sort(key=lambda m: m.created_at)
        ordered, linked = _order_by_edges(cluster_mems, edge_pairs)
        label = cluster_key or "(unclustered)"
        lines = []
        for m in ordered:
            connector = "↳ " if m.id in linked else ""
            lines.append(f"│       {connector}{_emoji(m.kind)} {m.title}")
        recency = max(m.created_at for m in cluster_mems)
        blocks.append((label, lines, recency))

    # Newest cluster first within the session.
    blocks.sort(key=lambda b: b[2], reverse=True)
    return [(label, lines) for label, lines, _ in blocks]


def render_panel(
    memories,
    *,
    session_topics: dict,
    edges,
    rules,
    role: str | None = None,
    watch=None,
    token_budget: int = 1800,
) -> str:
    """Render the SessionStart panel.

    memories: objects/dicts with id, title, kind, source_session,
        cluster, created_at.
    session_topics: {session_id: {"topic": str, "created_at": str}}.
    edges: iterable of (source_id, target_id, edge_type).
    rules: objects/dicts with text, severity, scope — rendered in full,
        never trimmed (per the always-inject-rules requirement).
    role: one-line role/purpose statement.
    watch: list of short caution strings.
    token_budget: oldest whole sessions, then oldest clusters, are dropped to
        fit; the header and CONFLICTS sections are never trimmed.
    """
    mems = [_Mem(m) for m in memories]
    edge_pairs = {(s, t) for s, t, _ in edges}
    contradicts = [(s, t) for s, t, kind in edges if kind == "contradicts"]

    # --- Header (never trimmed) --------------------------------------------
    rule_list = list(rules)
    title = f"╭─ memesis · {len(mems)} memories · {len(rule_list)} rules "
    header = [title.ljust(_WIDTH, "─")]
    if role:
        header.append(f"│ 🧭 ROLE   {role}")
    if rule_list:
        header.append("│ ⚖️  RULES")
        for r in rule_list:
            tag = _SEVERITY_TAG.get(_get(r, "severity"), "•")
            scope = _get(r, "scope") or "global"
            header.append(f"│   {tag} [{scope}] {_get(r, 'text')}")
    for item in watch or []:
        header.append(f"│ ⚠️  WATCH  {item}")

    # --- Body: sessions → clusters → memories ------------------------------
    by_session: dict = {}
    for m in mems:
        by_session.setdefault(m.session, []).append(m)

    def _session_sortkey(sid: str):
        topic = session_topics.get(sid) or {}
        if topic.get("created_at"):
            return str(topic["created_at"])
        return min((m.created_at for m in by_session[sid]), default="")

    session_blocks: list[tuple[str, list[tuple[str, list[str]]]]] = []
    for sid in sorted(by_session, key=_session_sortkey):
        topic = session_topics.get(sid) or {}
        if topic.get("topic"):
            label = f"{topic['topic']} · {_date_of(topic.get('created_at'))}"
        else:
            earliest = min((m.created_at for m in by_session[sid]), default=None)
            label = f"untitled · {_date_of(earliest)}"
        clusters = _build_clusters(by_session[sid], edge_pairs)
        session_blocks.append((label, clusters))

    # --- Conflicts footer (never trimmed) ----------------------------------
    title_by_id = {m.id: m.title for m in mems}
    conflict_lines: list[str] = []
    for s, t in contradicts:
        if s in title_by_id and t in title_by_id:
            conflict_lines.append(f'│   ✕ "{title_by_id[s]}"  ⇄  "{title_by_id[t]}"')

    legend = "╰─ legend  " + "  ".join(
        f"{e}{k}" for k, e in KIND_EMOJI.items()
    )

    # --- Token-budget trim -------------------------------------------------
    fixed = "\n".join(header + conflict_lines + [legend])
    fixed_tokens = estimate_tokens(fixed)

    def _body_lines(blocks):
        out: list[str] = []
        for label, clusters in blocks:
            out.extend(_render_session_block(label, clusters))
        return out

    trimmed_sessions = 0
    while session_blocks and estimate_tokens(
        "\n".join(_body_lines(session_blocks))
    ) + fixed_tokens > token_budget:
        # Drop the oldest session first; if only one remains, drop its
        # oldest cluster instead so the panel still fits.
        if len(session_blocks) > 1:
            session_blocks.pop(0)
            trimmed_sessions += 1
        else:
            label, clusters = session_blocks[0]
            if len(clusters) > 1:
                clusters.pop()  # clusters are newest-first → pop oldest
                session_blocks[0] = (label, clusters)
            else:
                break

    body = _body_lines(session_blocks)
    if trimmed_sessions:
        body.insert(0, f"│ … {trimmed_sessions} earlier session(s) trimmed")

    panel = header + body
    if conflict_lines:
        panel.append(_rule("⛔ CONFLICTS"))
        panel.extend(conflict_lines)
    panel.append(legend)
    return "\n".join(panel)


def build_role_line(role: str | None) -> str:
    """Fallback role line when the self-model has none."""
    return role or f"memesis memory session · {datetime.now():%Y-%m-%d}"


# Stage display config: (header_label, icon)
_STAGE_DISPLAY = [
    ("instinctive",  "⚡ instinctive"),
    ("crystallized", "💎 crystallized"),
    ("consolidated", "🔬 consolidated"),
    ("ephemeral",    "🌱 ephemeral"),
]


def render_index(
    memories,
    *,
    rules,
    role: str | None = None,
    watch=None,
    edges=(),
    token_budget: int = 1800,
) -> str:
    """Render a compact stage-grouped memory index for SessionStart.

    Each line: ``[id8] emoji title``
    Grouped by lifecycle stage (instinctive first, ephemeral last).
    Oldest entries first within each stage; budget trim drops the oldest
    consolidated entries first.
    """
    mems = [_Mem(m) for m in memories]
    contradicts = [(s, t) for s, t, kind in edges if kind == "contradicts"]

    # --- Header (never trimmed) ---
    rule_list = list(rules)
    title = f"╭─ memesis · {len(mems)} memories · {len(rule_list)} rules "
    header = [title.ljust(_WIDTH, "─")]
    if role:
        header.append(f"│ 🧭 ROLE   {role}")
    if rule_list:
        header.append("│ ⚖️  RULES")
        for r in rule_list:
            tag = _SEVERITY_TAG.get(_get(r, "severity"), "•")
            scope = _get(r, "scope") or "global"
            header.append(f"│   {tag} [{scope}] {_get(r, 'text')}")
    for item in watch or []:
        header.append(f"│ ⚠️  WATCH  {item}")

    # --- Group by stage ---
    by_stage: dict[str, list[_Mem]] = {}
    for m in mems:
        stage = _get(m, "stage") or "consolidated"
        by_stage.setdefault(stage, []).append(m)

    # Sort each stage oldest-first (recent at bottom).
    for stage_mems in by_stage.values():
        stage_mems.sort(key=lambda m: m.created_at)

    # --- Conflicts footer (never trimmed) ---
    title_by_id = {m.id: m.title for m in mems}
    conflict_lines: list[str] = []
    for s, t in contradicts:
        if s in title_by_id and t in title_by_id:
            conflict_lines.append(f'│   ✕ "{title_by_id[s]}"  ⇄  "{title_by_id[t]}"')

    legend = "╰─ legend  " + "  ".join(f"{e}{k}" for k, e in KIND_EMOJI.items())

    fixed = "\n".join(header + conflict_lines + [legend])
    fixed_tokens = estimate_tokens(fixed)
    budget_remaining = token_budget - fixed_tokens

    # Build stage blocks; trim consolidated from the oldest end if over budget.
    def _stage_block(stage_label: str, stage_mems: list[_Mem]) -> list[str]:
        lines = [_rule(f"{stage_label} ({len(stage_mems)})")]
        for m in stage_mems:
            short_id = str(m.id)[:8]
            lines.append(f"│   [{short_id}] {_emoji(m.kind)} {m.title}")
        return lines

    body_parts: list[tuple[str, list[_Mem]]] = []
    for stage_key, label in _STAGE_DISPLAY:
        if stage_key in by_stage:
            body_parts.append((label, by_stage[stage_key]))

    # Trim consolidated from oldest until it fits.
    while True:
        body_lines: list[str] = []
        for label, stage_mems in body_parts:
            body_lines.extend(_stage_block(label, stage_mems))
        if estimate_tokens("\n".join(body_lines)) <= budget_remaining:
            break
        # Find consolidated block and trim its oldest entry.
        for i, (label, stage_mems) in enumerate(body_parts):
            if "consolidated" in label and len(stage_mems) > 1:
                body_parts[i] = (label, stage_mems[1:])  # drop oldest
                break
        else:
            break  # nothing left to trim

    panel = header + body_lines
    if conflict_lines:
        panel.append(_rule("⛔ CONFLICTS"))
        panel.extend(conflict_lines)
    panel.append(legend)
    return "\n".join(panel)
