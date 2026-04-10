#!/usr/bin/env python3
"""
Memesis memory health dashboard.

Usage:
    python3 scripts/dashboard.py                    # Interactive TUI dashboard
    python3 scripts/dashboard.py --db eval/eval-observations.db  # Custom DB
    python3 scripts/dashboard.py --classic           # Legacy static output
    python3 scripts/dashboard.py --judge             # Include LLM judge scores (classic only)
"""

import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Data collection (shared across all rendering modes)
# ---------------------------------------------------------------------------

def load_observations(db_path: str) -> tuple[list[dict], int]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, title, content, observation_type, tags, count, sources, created_at "
        "FROM observations ORDER BY count DESC"
    ).fetchall()
    processed = conn.execute("SELECT COUNT(*) FROM processed_sessions").fetchone()[0]
    conn.close()
    obs = []
    for r in rows:
        obs.append({
            "id": r[0], "title": r[1], "content": r[2], "type": r[3],
            "tags": json.loads(r[4]) if r[4] else [], "count": r[5],
            "sources": json.loads(r[6]) if r[6] else [], "created_at": r[7],
        })
    return obs, processed


def load_session_index() -> dict:
    """Build session_id -> {project, snippet, lines} index from summaries.

    Parses [USER:L7] / [CLAUDE:L15] tags so the dashboard can show the
    exact transcript line that evidenced an observation, not just the
    session's opening message.
    """
    import re
    summaries_dir = Path(__file__).parent.parent / "backfill-output"
    line_tag_re = re.compile(r'\[(USER|CLAUDE):L(\d+)\]\s*')
    index = {}
    for f in sorted(summaries_dir.glob("summaries-*.jsonl")):
        project = f.stem.replace("summaries-", "")
        with open(f) as fh:
            for raw_line in fh:
                s = json.loads(raw_line)
                sid = s["session_id"]
                summary = s.get("summary", "")

                # Parse line-keyed messages: {line_num: (role, text)}
                lines_map = {}
                first_user_snippet = ""
                for m in line_tag_re.finditer(summary):
                    role = m.group(1)
                    line_num = int(m.group(2))
                    # Text runs from end of this tag to start of next tag
                    start = m.end()
                    next_m = line_tag_re.search(summary, start)
                    end = next_m.start() if next_m else len(summary)
                    text = summary[start:end].strip()
                    # Strip [thinking] prefix from CLAUDE lines
                    if text.startswith("[thinking]"):
                        text = text[len("[thinking]"):].strip()
                    lines_map[line_num] = (role, text[:300])
                    if role == "USER" and not first_user_snippet:
                        first_user_snippet = text[:200]

                if not first_user_snippet:
                    first_user_snippet = summary[:200]

                index[sid] = {
                    "project": project,
                    "snippet": first_user_snippet,
                    "lines": lines_map,
                }
    return index


def compute_stage_distribution(observations: list[dict]) -> dict:
    stages = {"crystallized": [], "consolidated": []}
    for o in observations:
        if o["count"] >= 10:
            stages["crystallized"].append(o)
        else:
            stages["consolidated"].append(o)
    return stages


def deep_find_duplicates(observations: list[dict], threshold: float = 0.30) -> list[tuple]:
    """Find candidate duplicates using combined title + content similarity.

    Uses SequenceMatcher on content bodies (not just title word overlap) to
    catch pairs that say the same thing with different wording. Scores are
    weighted 0.3 title + 0.7 content so content drives the ranking.
    """
    pairs = []
    normed = [(o, o["title"].lower(), (o["content"] or "").lower()[:600]) for o in observations]
    for i, (a, at, ac) in enumerate(normed):
        for j in range(i + 1, len(normed)):
            b, bt, bc = normed[j]
            wa, wb = set(at.split()), set(bt.split())
            if wa and wb and not (wa & wb):
                continue
            title_sim = SequenceMatcher(None, at, bt).ratio()
            content_sim = SequenceMatcher(None, ac, bc).ratio() if ac and bc else 0.0
            score = 0.3 * title_sim + 0.7 * content_sim
            if score >= threshold:
                pairs.append((score, title_sim, content_sim, a, b))
    pairs.sort(key=lambda x: x[0], reverse=True)
    return pairs


def type_distribution(observations: list[dict]) -> dict:
    counts = Counter(o["type"] or "untyped" for o in observations)
    return dict(counts.most_common())


def compute_quality_concerns(stages: dict) -> dict:
    issues = []
    for o in stages["crystallized"]:
        content = o["content"] or ""
        if len(content) < 80:
            issues.append(("short", o))
        elif any(p in content for p in [".md", ".py", ".json", "worktree", "directory"]):
            if o["type"] in ("workflow_pattern",) and "style" not in content.lower():
                issues.append(("codebase?", o))
    singles = [o for o in stages["consolidated"] if o["count"] == 1]
    total_chars = sum(len(o["content"] or "") for o in stages["crystallized"])
    budget_8pct = int(0.08 * 200000 * 4)
    return {
        "issues": issues,
        "singles_count": len(singles),
        "singles_pct": len(singles) / max(len(stages["consolidated"]), 1),
        "cryst_chars": total_chars,
        "budget": budget_8pct,
        "fits": total_chars < budget_8pct,
    }


# ---------------------------------------------------------------------------
# Textual TUI
# ---------------------------------------------------------------------------

TYPE_COLORS = {
    "workflow_pattern": "cyan",
    "decision_context": "dodger_blue2",
    "collaboration_dynamic": "yellow",
    "preference_signal": "green",
    "self_observation": "magenta",
    "personality": "red",
    "aesthetic": "orchid1",
    "correction": "dark_orange",
    "communication_style": "gold1",
}


def run_tui(db_path: str):
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.reactive import reactive
    from textual.widget import Widget
    from textual.widgets import (
        DataTable, Footer, Input, Label, Static, TabbedContent, TabPane,
    )
    from rich.text import Text

    # --- Load data up front (dupes computed async) ---
    session_index = load_session_index()
    observations, processed = load_observations(db_path)
    stages = compute_stage_distribution(observations)
    types = type_distribution(observations)
    concerns = compute_quality_concerns(stages)
    dupes: list[tuple] = []
    dupe_ids: set[int] = set()

    total = len(observations)
    total_chars = sum(len(o["content"] or "") for o in observations)
    total_reinforcements = sum(o["count"] for o in observations)
    avg_count = total_reinforcements / max(total, 1)
    cryst_n = len(stages["crystallized"])
    consol_n = len(stages["consolidated"])

    # Compute table width from data: fixed cols (4+6+22) + longest title + padding
    max_title_len = max((len(o["title"]) for o in observations), default=40)
    table_col_width = 4 + 6 + 22 + max_title_len + 8

    # --- Custom widgets ---

    class TypeChart(Static):
        DEFAULT_CSS = "TypeChart { height: auto; padding: 0 1; overflow: hidden; }"
        def render(self) -> Text:
            t = Text(no_wrap=True, overflow="ellipsis")
            max_count = max(types.values()) if types else 1
            bar_width = max(10, self.size.width - 30)
            for type_name, count in types.items():
                w = max(1, int(count / max_count * bar_width))
                color = TYPE_COLORS.get(type_name, "white")
                t.append(f"  {type_name:<22s} ", style="dim")
                t.append("\u2588" * w, style=color)
                t.append(f" {count}\n", style="dim")
            return t

    class QualityConcerns(Static):
        DEFAULT_CSS = "QualityConcerns { height: auto; padding: 0 1; overflow: hidden; }"
        def render(self) -> Text:
            t = Text(no_wrap=True, overflow="ellipsis")
            c = concerns
            if c["issues"]:
                t.append(f"  {len(c['issues'])} crystallized may be codebase-derivable:\n", style="yellow")
                for reason, o in c["issues"]:
                    t.append(f"    [{reason:9s}] ", style="yellow dim")
                    t.append(f"x{o['count']:>3d} {o['title']}\n", style="dim")
            else:
                t.append("  No quality concerns in crystallized tier\n", style="green dim")
            t.append(f"\n  {c['singles_count']} single-occurrence (", style="dim")
            t.append(f"{c['singles_pct']:.0%}", style="yellow")
            t.append(" of consolidated)\n", style="dim")
            t.append(f"  Crystallized: {c['cryst_chars']:,} chars", style="dim")
            t.append(f" | budget: {c['budget']:,} chars ", style="dim")
            t.append("fits" if c["fits"] else "exceeds", style="green" if c["fits"] else "red")
            return t

    class DetailPane(Static):
        DEFAULT_CSS = "DetailPane { height: auto; padding: 1 2; background: $surface; }"

        def __init__(self) -> None:
            super().__init__("[dim]Select a row to view details[/dim]")

        def show_observation(self, obs_id: int) -> None:
            obs = next((o for o in observations if o["id"] == obs_id), None)
            if not obs:
                self.update("[dim]Select a row to view details[/dim]")
                return
            stage = "crystallized" if obs["count"] >= 10 else "consolidated"
            stage_color = "magenta" if stage == "crystallized" else "green"
            is_dupe = obs["id"] in dupe_ids

            t = Text()
            t.append(f"#{obs['id']}", style="dim")
            t.append(f"  {stage.upper()}", style=stage_color)
            t.append(f"  x{obs['count']}", style="bold")
            if is_dupe:
                t.append("  DUPE CANDIDATE", style="bold red")
            t.append(f"\n\n{obs['title']}\n\n", style="bold")
            t.append(f"type: {obs['type'] or 'untyped'}", style="dim")
            if obs["tags"]:
                t.append(f"\ntags: {', '.join(obs['tags'])}", style="dim")
            if obs.get("created_at"):
                t.append(f"\ncreated: {obs['created_at'][:10]}", style="dim")
            sources = obs.get("sources", [])
            t.append(f"\nsources: {len(sources)} sessions", style="dim")
            t.append("\n\n")
            t.append(obs["content"] or "(no content)", style="")

            partners = [(s, ts, cs, a, b) for s, ts, cs, a, b in dupes
                        if a["id"] == obs_id or b["id"] == obs_id]
            if partners:
                t.append(f"\n\n--- Duplicate Partners ({len(partners)}) ---\n", style="bold red")
                for score, tsim, csim, a, b in partners[:8]:
                    other = b if a["id"] == obs_id else a
                    t.append(f"  {score:.0%}", style="red bold")
                    t.append(f" (t:{tsim:.0%} c:{csim:.0%})", style="dim")
                    t.append(f"  #{other['id']} {other['title']}\n", style="")

            self.update(t)

    class SourcesPane(Static):
        DEFAULT_CSS = "SourcesPane { height: auto; padding: 1 2; background: $surface; }"

        def __init__(self) -> None:
            super().__init__("[dim]Select a row to view sources[/dim]")

        def show_sources(self, obs_id: int) -> None:
            obs = next((o for o in observations if o["id"] == obs_id), None)
            if not obs:
                self.update("[dim]Select a row to view sources[/dim]")
                return
            sources = obs.get("sources", [])
            if not sources:
                self.update("[dim]No sources recorded[/dim]")
                return

            t = Text()
            t.append(f"#{obs['id']}  ", style="dim")
            t.append(f"{obs['title']}\n", style="bold")
            t.append(f"{len(sources)} source sessions\n\n", style="dim")

            for src in sources:
                if isinstance(src, str):
                    sid, src_lines, action, rationale, confidence = src, None, None, None, None
                elif isinstance(src, dict):
                    sid = src.get("session", "?")
                    src_lines = src.get("lines")
                    action = src.get("action")
                    rationale = src.get("rationale")
                    confidence = src.get("confidence")
                else:
                    continue
                info = session_index.get(sid)

                # Header line
                t.append(f"  {sid[:8]}", style="cyan")
                if action:
                    action_style = "green bold" if action == "create" else "blue" if action == "reinforce" else "yellow"
                    t.append(f" {action}", style=action_style)
                if confidence:
                    conf_style = "green" if confidence == "high" else "yellow" if confidence == "medium" else "red"
                    t.append(f" [{confidence}]", style=conf_style)
                if info:
                    t.append(f"  [{info['project']}]", style="dim")
                if isinstance(src, dict) and src.get("at"):
                    t.append(f"  {src['at'][:10]}", style="dim")

                # Rationale
                if rationale:
                    t.append(f"\n  \"{rationale}\"", style="italic")

                # Cited transcript lines
                if src_lines and info and info.get("lines"):
                    for ln in src_lines[:3]:
                        entry = info["lines"].get(ln)
                        if entry:
                            role, text = entry
                            role_style = "yellow" if role == "USER" else "dim"
                            t.append(f"\n  L{ln} ", style="yellow bold")
                            t.append(f"[{role}] ", style=role_style)
                            t.append(f"{text[:200]}", style="dim italic")
                        else:
                            t.append(f"\n  L{ln} (line not in summary)", style="dim")
                elif info:
                    t.append(f"\n  {info['snippet']}", style="dim italic")
                else:
                    t.append("  (summary not found)", style="dim")
                t.append("\n\n")

            self.update(t)

    # --- Main App ---

    class MemesisDashboard(App):
        TITLE = "MEMESIS"
        SUB_TITLE = f"memory health \u2014 {db_path}"
        CSS = """
        Screen { background: $background; }
        #main-area { height: 1fr; }
        #table-col { width: """ + str(table_col_width) + """; }
        #detail-col { width: 1fr; }
        #search-bar {
            dock: top; height: 3; padding: 0 1; background: $surface;
        }
        #search-bar Input { width: 1fr; }
        #search-bar .filter-label { width: auto; padding: 0 1; }
        #status-line {
            dock: bottom; height: 1; padding: 0 1;
            background: $surface; color: $text-muted;
        }
        DataTable { height: 1fr; }
        DataTable > .datatable--header { text-style: bold; }
        DataTable > .datatable--cursor { background: $accent; }
        TabbedContent { height: 1fr; }
        TabPane { padding: 0; }
        #dupes-tab { height: 1fr; overflow-y: auto; padding: 0 1; }
        .section-title {
            text-style: bold; color: $text-muted; padding: 0 1;
        }
        """

        ALLOW_SELECT = True

        BINDINGS = [
            Binding("ctrl+c", "copy_text", "Copy", show=False),
            Binding("ctrl+d", "quit", "Quit", show=False),
            Binding("q", "quit", "Quit"),
            Binding("/", "focus_search", "Search"),
            Binding("escape", "clear_search", "Clear"),
            Binding("1", "filter_all", "All"),
            Binding("2", "filter_cryst", "Crystallized"),
            Binding("3", "filter_consol", "Consolidated"),
            Binding("d", "show_tab('dupes-pane')", "Dupes"),
            Binding("s", "show_tab('sources-pane')", "Sources"),
            Binding("o", "show_tab('overview-pane')", "Overview"),
        ]

        _filter_stage = reactive("all", init=False)
        _search_query = reactive("", init=False)

        def compose(self) -> ComposeResult:
            with Horizontal(id="main-area"):
                with Vertical(id="table-col"):
                    with Horizontal(id="search-bar"):
                        yield Label(
                            f"[bold]1[/bold] All({total})  "
                            f"[bold]2[/bold] [magenta]Cryst({cryst_n})[/magenta]  "
                            f"[bold]3[/bold] [green]Consol({consol_n})[/green]  "
                            f"[bold]/[/bold] Search  "
                            f"[bold]d[/bold]/[bold]o[/bold] Tabs",
                            classes="filter-label",
                        )
                        yield Input(placeholder="Filter by title or content...", id="search-input")
                    yield DataTable(id="mem-table", zebra_stripes=True, cursor_type="row")
                with Vertical(id="detail-col"):
                    with TabbedContent(initial="overview-pane"):
                        with TabPane("Overview", id="overview-pane"):
                            with VerticalScroll():
                                yield Label("[bold dim]OBSERVATION TYPES[/bold dim]", classes="section-title")
                                yield TypeChart()
                                yield Label("", classes="section-title")
                                yield Label("[bold dim]QUALITY CONCERNS[/bold dim]", classes="section-title")
                                yield QualityConcerns()
                        with TabPane("Dupes (computing...)", id="dupes-pane"):
                            yield VerticalScroll(Static("[dim]Computing duplicates...[/dim]", id="dupes-content"), id="dupes-tab")
                        with TabPane("Detail", id="detail-pane"):
                            with VerticalScroll():
                                yield DetailPane()
                        with TabPane("Sources", id="sources-pane"):
                            with VerticalScroll():
                                yield SourcesPane()
            cryst_pct = cryst_n / max(total, 1)
            consol_pct = consol_n / max(total, 1)
            fits_str = "[green]fits[/green]" if concerns["fits"] else f"[red]exceeds by {concerns['cryst_chars'] - concerns['budget']:,}[/red]"
            yield Static(
                f"  [bold]{total}[/bold] obs  "
                f"[magenta]{cryst_n}[/magenta] cryst ({cryst_pct:.0%})  "
                f"[green]{consol_n}[/green] consol ({consol_pct:.0%})  "
                f"[dim]|[/dim]  "
                f"[cyan]{processed}[/cyan] sessions  "
                f"[dim]{total_chars:,} chars[/dim]  "
                f"[dim]avg reinf {avg_count:.1f}[/dim]  "
                f"[dim]|[/dim]  "
                f"[yellow]{len(dupes)}[/yellow] dupe pairs  "
                f"[dim]budget: {fits_str}[/dim]",
                id="status-line",
            )
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#mem-table", DataTable)
            table.add_column("ct", width=4)
            table.add_column("stage", width=6)
            table.add_column("type", width=22)
            table.add_column("title")
            self._populate_table()
            self.run_worker(self._compute_dupes, thread=True)

        def _populate_table(self) -> None:
            table = self.query_one("#mem-table", DataTable)
            table.clear()
            filtered = observations
            if self._filter_stage == "crystallized":
                filtered = [o for o in filtered if o["count"] >= 10]
            elif self._filter_stage == "consolidated":
                filtered = [o for o in filtered if o["count"] < 10]
            q = self._search_query.lower()
            if q:
                filtered = [o for o in filtered
                            if q in o["title"].lower() or q in (o["content"] or "").lower()]
            for o in filtered:
                stage = "crystallized" if o["count"] >= 10 else "consolidated"
                stage_color = "magenta" if stage == "crystallized" else "green"
                obs_type = o["type"] or "untyped"
                type_color = TYPE_COLORS.get(obs_type, "white")
                is_dupe = o["id"] in dupe_ids
                title_text = Text(o["title"])
                if is_dupe:
                    title_text.append(" \u25cf", style="red bold")
                table.add_row(
                    Text(str(o["count"]), style="bold"),
                    Text(stage[:5].upper(), style=stage_color),
                    Text(obs_type, style=type_color),
                    title_text,
                    key=str(o["id"]),
                )

        def _render_dupes(self) -> None:
            content = self.query_one("#dupes-content", Static)
            # Update tab title now that computation is done
            try:
                tab = self.query_one("Tab#--content-tab-dupes-pane")
                tab.label = f"Dupes ({len(dupes)})"
            except Exception:
                pass
            t = Text()
            t.append(f"Deep search: {len(dupes)} pairs (title+content SequenceMatcher)\n\n", style="dim")
            for i, (score, tsim, csim, a, b) in enumerate(dupes[:40]):
                rank_style = "bold red" if score >= 0.5 else "yellow" if score >= 0.4 else "dim"
                t.append(f"  {score:>4.0%}", style=rank_style)
                t.append(f"  t:{tsim:.0%} c:{csim:.0%}", style="dim")
                t.append(f"\n    A: #{a['id']:<4d}", style="dim")
                t.append(f"{a['title']}\n", style="")
                t.append(f"    B: #{b['id']:<4d}", style="dim")
                t.append(f"{b['title']}\n\n", style="")
            if len(dupes) > 40:
                t.append(f"  ... +{len(dupes) - 40} more pairs", style="dim")
            content.update(t)

        def _compute_dupes(self) -> None:
            """Run expensive dupe detection in a worker thread."""
            nonlocal dupes, dupe_ids
            result = deep_find_duplicates(observations)
            dupes.clear()
            dupes.extend(result)
            dupe_ids.clear()
            for _, _, _, a, b in dupes:
                dupe_ids.add(a["id"])
                dupe_ids.add(b["id"])
            self.call_from_thread(self._render_dupes)
            self.call_from_thread(self._populate_table)

        def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
            row_key = event.row_key.value
            if row_key is not None:
                obs_id = int(row_key)
                obs = next((o for o in observations if o["id"] == obs_id), None)
                self.query_one(DetailPane).show_observation(obs_id)
                self.query_one(SourcesPane).show_sources(obs_id)
                if obs:
                    n = len(obs.get("sources", []))
                    try:
                        tab = self.query_one("Tab#--content-tab-sources-pane")
                        tab.label = f"Sources ({n})"
                    except Exception:
                        pass
                self.query_one(TabbedContent).active = "detail-pane"

        _search_timer = None

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id == "search-input":
                if self._search_timer:
                    self._search_timer.stop()
                value = event.value
                self._search_timer = self.set_timer(
                    0.3, lambda: setattr(self, '_search_query', value)
                )

        def watch__filter_stage(self) -> None:
            self._populate_table()

        def watch__search_query(self) -> None:
            self._populate_table()

        def action_focus_search(self) -> None:
            self.query_one("#search-input", Input).focus()

        def action_clear_search(self) -> None:
            self.query_one("#search-input", Input).value = ""
            self._filter_stage = "all"
            self.query_one("#mem-table", DataTable).focus()

        def action_filter_all(self) -> None:
            self._filter_stage = "all"
        def action_filter_cryst(self) -> None:
            self._filter_stage = "crystallized"
        def action_filter_consol(self) -> None:
            self._filter_stage = "consolidated"
        def action_show_tab(self, tab_id: str) -> None:
            self.query_one(TabbedContent).active = tab_id

    MemesisDashboard().run()


# ---------------------------------------------------------------------------
# Classic (rich print) output — moved behind --classic
# ---------------------------------------------------------------------------

def run_classic(db_path: str, run_judges: bool = False):
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.columns import Columns
    from rich import box

    console = Console()
    observations, processed = load_observations(db_path)
    stages = compute_stage_distribution(observations)
    types = type_distribution(observations)
    dupes = deep_find_duplicates(observations)

    total = len(observations)
    total_chars = sum(len(o["content"] or "") for o in observations)
    total_reinforcements = sum(o["count"] for o in observations)
    avg_count = total_reinforcements / max(total, 1)
    cryst_n = len(stages["crystallized"])
    consol_n = len(stages["consolidated"])

    t = Text()
    t.append("MEMESIS", style="bold bright_white")
    t.append("  memory health dashboard", style="dim")
    t.append(f"\n{db_path}", style="dim italic")
    t.append(f"  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}", style="dim")
    console.print(Panel(t, box=box.SIMPLE, style="dim"))

    ov = Table(show_header=False, box=None, padding=(0, 2))
    ov.add_column(style="dim", width=22)
    ov.add_column(style="bright_white", width=10, justify="right")
    ov.add_column(style="dim", width=22)
    ov.add_column(style="bright_white", width=10, justify="right")
    ov.add_row("Observations", str(total), "Sessions processed", str(processed))
    ov.add_row("Total chars", f"{total_chars:,}", "Avg reinforcements", f"{avg_count:.1f}")
    ov.add_row("Crystallized", f"[magenta]{cryst_n}[/magenta]", "Consolidated", f"[green]{consol_n}[/green]")
    console.print(Panel(ov, title="[bold]Overview[/bold]", border_style="bright_black", box=box.ROUNDED))

    bar_width = 60
    cryst_w = max(1, int(cryst_n / total * bar_width)) if cryst_n else 0
    consol_w = bar_width - cryst_w
    sb = Text()
    sb.append("  \u2588" * cryst_w, style="magenta")
    sb.append("\u2588" * consol_w, style="green")
    sb.append(f"\n  crystallized {cryst_n} ({cryst_n/total:.0%})", style="magenta")
    sb.append(f"{'':>{bar_width - 24}}")
    sb.append(f"consolidated {consol_n} ({consol_n/total:.0%})", style="green")
    console.print(Panel(sb, title="[bold]Stage Distribution[/bold]", border_style="bright_black", box=box.ROUNDED))

    max_count = max(types.values()) if types else 1
    tc = Table(show_header=False, box=None, padding=(0, 1))
    tc.add_column(width=24, style="dim"); tc.add_column(width=36)
    for type_name, count in types.items():
        w = max(1, int(count / max_count * 30))
        color = TYPE_COLORS.get(type_name, "white")
        bar = Text(); bar.append("\u2588" * w, style=color); bar.append(f" {count}", style="dim")
        tc.add_row(type_name[:24], bar)
    type_panel = Panel(tc, title="[bold]Observation Types[/bold]", border_style="bright_black", box=box.ROUNDED)

    qc = compute_quality_concerns(stages)
    qt = Text()
    if qc["issues"]:
        qt.append(f"  {len(qc['issues'])} crystallized may be codebase-derivable:\n", style="yellow")
        for reason, o in qc["issues"][:5]:
            qt.append(f"    [{reason:9s}] x{o['count']:3d} {o['title'][:50]}\n", style="dim")
    else:
        qt.append("  No obvious quality concerns\n", style="green dim")
    qt.append(f"\n  {qc['singles_count']} singles ({qc['singles_pct']:.0%} of consolidated)\n", style="dim")
    qt.append(f"  Crystallized: {qc['cryst_chars']:,} / {qc['budget']:,} chars ", style="dim")
    qt.append("fits" if qc["fits"] else "exceeds", style="green" if qc["fits"] else "red")
    quality_panel = Panel(qt, title="[bold]Quality Concerns[/bold]", border_style="yellow", box=box.ROUNDED)
    console.print(Columns([type_panel, quality_panel], equal=True))

    if dupes:
        dt = Table(box=None, padding=(0, 1), show_edge=False)
        dt.add_column("score", width=6, style="red", justify="right")
        dt.add_column("t/c", width=10, style="dim")
        dt.add_column("A", ratio=1); dt.add_column("B", ratio=1)
        for score, tsim, csim, a, b in dupes[:10]:
            dt.add_row(f"{score:.0%}", f"{tsim:.0%}/{csim:.0%}",
                       f"[dim]#{a['id']}[/dim] {a['title'][:40]}",
                       f"[dim]#{b['id']}[/dim] {b['title'][:40]}")
        if len(dupes) > 10:
            dt.add_row("", "", f"[dim]... +{len(dupes) - 10} more[/dim]", "")
        console.print(Panel(dt, title=f"[bold]Deep Duplicate Search ({len(dupes)} pairs)[/bold]",
                            border_style="red", box=box.ROUNDED))
    console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    db_path = "eval/eval-observations.db"
    run_judges = False
    classic_mode = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--db" and i + 1 < len(args):
            db_path = args[i + 1]; i += 2
        elif args[i] == "--judge":
            run_judges = True; i += 1
        elif args[i] == "--classic":
            classic_mode = True; i += 1
        else:
            i += 1

    if not Path(db_path).exists():
        from rich.console import Console
        Console().print(f"[red]DB not found: {db_path}[/red]")
        sys.exit(1)

    if classic_mode:
        run_classic(db_path, run_judges)
    else:
        run_tui(db_path)


if __name__ == "__main__":
    main()
