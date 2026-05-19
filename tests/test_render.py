"""Tests for hooks/_render.py — SessionStart panel rendering (pure, no DB)."""

from __future__ import annotations

from hooks._render import KIND_EMOJI, _Mem, _order_by_edges, render_panel


def _mem(mid, title, kind="fact", session="s1", cluster=None,
         created_at="2026-05-17T10:00:00") -> dict:
    return {
        "id": mid, "title": title, "memory_kind": kind,
        "source_session": session, "cluster": cluster, "created_at": created_at,
    }


def _rule(text, severity="block", scope=None) -> dict:
    return {"text": text, "severity": severity, "scope": scope}


def test_panel_has_core_sections():
    panel = render_panel(
        [_mem("a", "First fact")],
        session_topics={"s1": {"topic": "Topic One", "created_at": "2026-05-17T09:00:00"}},
        edges=[], rules=[_rule("never rm -rf")],
        role="memory maintainer", watch=["mind the migration runner"],
    )
    assert "ROLE" in panel and "memory maintainer" in panel
    assert "RULES" in panel and "never rm -rf" in panel
    assert "WATCH" in panel and "mind the migration runner" in panel
    assert "Topic One" in panel and "First fact" in panel
    assert "legend" in panel


def test_rules_rendered_in_full():
    rules = [_rule("rule one"), _rule("rule two", severity="ask"),
             _rule("rule three", scope="memesis")]
    panel = render_panel([_mem("a", "x")], session_topics={}, edges=[], rules=rules)
    for text in ("rule one", "rule two", "rule three"):
        assert text in panel
    assert "[memesis]" in panel
    assert "[global]" in panel


def test_sessions_ordered_oldest_first():
    mems = [
        _mem("a", "Old mem", session="old", created_at="2026-05-10T10:00:00"),
        _mem("b", "New mem", session="new", created_at="2026-05-16T10:00:00"),
    ]
    topics = {
        "old": {"topic": "Older work", "created_at": "2026-05-10T09:00:00"},
        "new": {"topic": "Newer work", "created_at": "2026-05-16T09:00:00"},
    }
    panel = render_panel(mems, session_topics=topics, edges=[], rules=[])
    assert panel.index("Older work") < panel.index("Newer work")


def test_clusters_grouped_within_session():
    mems = [_mem("a", "Alpha", cluster="cluster-x"),
            _mem("b", "Beta", cluster="cluster-y")]
    panel = render_panel(mems, session_topics={}, edges=[], rules=[])
    assert "cluster-x" in panel and "cluster-y" in panel


def test_untitled_session_fallback():
    panel = render_panel(
        [_mem("a", "A", session="orphan", created_at="2026-05-12T10:00:00")],
        session_topics={}, edges=[], rules=[],
    )
    assert "untitled" in panel


def test_order_by_edges_groups_components():
    mems = [_Mem(_mem("a", "A")), _Mem(_mem("b", "B")), _Mem(_mem("c", "C"))]
    ordered, linked = _order_by_edges(mems, {("a", "c")})
    ids = [m.id for m in ordered]
    assert abs(ids.index("a") - ids.index("c")) == 1   # connected → contiguous
    assert "c" in linked and "a" not in linked          # non-first member marked
    assert "b" not in linked


def test_panel_marks_edge_links():
    mems = [_mem("a", "A", cluster="c", created_at="2026-05-17T10:00:00"),
            _mem("c", "C", cluster="c", created_at="2026-05-17T10:02:00")]
    panel = render_panel(mems, session_topics={}, edges=[("a", "c", "echo")], rules=[])
    assert "↳" in panel


def test_conflicts_footer_lists_contradictions():
    mems = [_mem("a", "Mem A"), _mem("b", "Mem B")]
    panel = render_panel(mems, session_topics={}, edges=[("a", "b", "contradicts")], rules=[])
    assert "CONFLICTS" in panel
    assert "Mem A" in panel and "Mem B" in panel and "⇄" in panel


def test_no_conflicts_footer_when_none():
    panel = render_panel([_mem("a", "A")], session_topics={}, edges=[], rules=[])
    assert "CONFLICTS" not in panel


def test_token_budget_trims_oldest_sessions():
    mems, topics = [], {}
    for s in range(6):
        sid = f"s{s}"
        topics[sid] = {"topic": f"Session {s} topic",
                       "created_at": f"2026-05-1{s}T09:00:00"}
        for m in range(4):
            mems.append(_mem(f"m{s}-{m}", f"Memory {s}-{m} with a longish title",
                             session=sid, created_at=f"2026-05-1{s}T10:0{m}:00"))
    panel = render_panel(mems, session_topics=topics, edges=[], rules=[], token_budget=80)
    assert "trimmed" in panel
    assert "Session 5 topic" in panel       # newest survives
    assert "Session 0 topic" not in panel   # oldest dropped


def test_legend_lists_kind_emoji():
    panel = render_panel([_mem("a", "A")], session_topics={}, edges=[], rules=[])
    assert "legend" in panel
    assert KIND_EMOJI["gotcha"] in panel
    assert KIND_EMOJI["invariant"] in panel
