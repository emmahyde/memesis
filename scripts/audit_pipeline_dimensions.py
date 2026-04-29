#!/usr/bin/env python3
"""
Audit pipeline output by dimension: PICKED / DROPPED / ENRICHED.

Reads the JSON report dumped by `run_selected_sessions.py --report`
and emits a markdown breakdown so a human can eyeball:

  PICKED    — every issue card + every orphan observation that survived
              to ephemeral, with full text, importance, kind, evidence
  DROPPED   — window skips by reason, content-hash dedup losses (if any),
              low-importance filter (NOTE: not currently in report JSON;
              would require instrumentation in extract_observations*)
  ENRICHED  — affect signals that actually boosted importance,
              issue-card synthesis notes, self-reflection rule fires

Usage:
    python scripts/audit_pipeline_dimensions.py REPORT_JSON [--out MD_PATH]
"""

import argparse
import json
from pathlib import Path
from textwrap import indent


def fmt_card(card: dict) -> str:
    imp = card.get("importance", 0)
    imp_str = f"{imp:.2f}" if isinstance(imp, (int, float)) else str(imp)
    lines = [
        f"- **{card.get('title', '(untitled)')}**  "
        f"(importance={imp_str}, "
        f"kind={card.get('kind', '?')}, "
        f"knowledge_type={card.get('knowledge_type', '?')}, "
        f"scope={card.get('scope', '?')})",
    ]
    problem = (card.get("problem") or "").strip()
    decision = (card.get("decision_or_outcome") or "").strip()
    if problem:
        lines.append(indent(f"**problem:** {problem}", "  > "))
    if decision:
        lines.append(indent(f"**outcome:** {decision}", "  > "))
    options = card.get("options_considered") or []
    if options:
        lines.append(f"  - options considered: {len(options)}")
        for opt in options[:4]:
            lines.append(f"    - {opt}")
    quotes = card.get("evidence_quotes") or []
    if quotes:
        lines.append(f"  - evidence quotes: {len(quotes)}")
        for q in quotes[:2]:
            qq = (q or "")[:160].replace("\n", " ")
            lines.append(f"    - `{qq}`")
    user_reaction = card.get("user_reaction")
    valence = card.get("user_affect_valence")
    if user_reaction or valence:
        lines.append(f"  - user_reaction: {user_reaction!r} (valence={valence})")
    return "\n".join(lines)


def fmt_obs(obs: dict) -> str:
    imp = obs.get("importance", 0)
    imp_str = f"{imp:.2f}" if isinstance(imp, (int, float)) else str(imp)
    # Resolve display text: text > content > body > summary > joined facts
    raw_text = (
        obs.get("text")
        or obs.get("content")
        or obs.get("body")
        or obs.get("summary")
        or ""
    )
    if not raw_text:
        facts = obs.get("facts") or []
        raw_text = "; ".join(str(f) for f in facts if f)
    body = raw_text.strip()
    if len(body) > 160:
        body = body[:160] + "..."
    lines = [
        f"- **[{obs.get('kind', '?')}]**  "
        f"(importance={imp_str}, "
        f"knowledge_type={obs.get('knowledge_type', '?')}, "
        f"scope={obs.get('scope', '?')})",
    ]
    if body:
        lines.append(indent(body, "  > "))
    quotes = obs.get("evidence_quotes") or []
    if quotes:
        lines.append(f"  - evidence quotes: {len(quotes)}")
        for q in quotes[:2]:
            qq = (q or "")[:160].replace("\n", " ")
            lines.append(f"    - `{qq}`")
    return "\n".join(lines)


def fmt_skip(skip: dict, idx: int) -> str:
    win = skip.get("window_index", skip.get("window", idx))
    outcome = skip.get("outcome", "?")
    valence = skip.get("affect_valence", "neutral")
    intensity = skip.get("affect_intensity", 0.0)
    return (
        f"- window {win}: **{outcome}** "
        f"(affect: {valence}/{intensity:.2f})"
    )


def fmt_affect(a: dict, idx: int) -> str:
    boost = a.get("max_boost", 0)
    if boost <= 0:
        return ""
    quotes = a.get("evidence_quotes") or []
    flags = []
    if a.get("has_repetition"):
        flags.append("repetition")
    if a.get("has_pushback"):
        flags.append("pushback")
    flag_str = f" [{', '.join(flags)}]" if flags else ""
    line = (
        f"- window {idx}: valence=**{a.get('valence', '?')}** "
        f"boost=+{boost:.2f} "
        f"prior=+{a.get('importance_prior', 0):.2f}"
        f"{flag_str} "
        f"(turns={a.get('user_turn_count', 0)}/"
        f"nontrivial={a.get('nontrivial_turn_count', 0)})"
    )
    if quotes:
        q = (quotes[0] or "")[:140].replace("\n", " ")
        line += f"\n  > `{q}`"
    return line


def fmt_self_obs(s: dict) -> str:
    rid = s.get("rule_id", "?")
    kind = s.get("kind", "?")
    ktype = s.get("knowledge_type", "?")
    imp = s.get("importance", 0)
    facts = s.get("facts") or []
    action = (s.get("proposed_action") or "").strip()
    lines = [
        f"- **{rid}** "
        f"(kind={kind}, knowledge_type={ktype}, importance={imp:.2f})"
    ]
    for f in facts:
        lines.append(indent(f.strip(), "  > "))
    if action:
        lines.append(f"  - **proposed_action:** {action}")
    return "\n".join(lines)


def render_session(sid: str, rec: dict) -> str:
    out: list[str] = []
    out.append(f"## {sid}\n")
    out.append(
        f"**type**: `{rec.get('session_type', '?')}` · "
        f"**mode**: `{rec.get('mode', '?')}` · "
        f"**chunking**: implied · "
        f"**windows**: {rec.get('windows', 0)} "
        f"({rec.get('productive_windows', 0)} productive) · "
        f"**cwd**: `{rec.get('cwd', '?')}`\n"
    )
    out.append(
        f"**counts**: raw={rec.get('raw_count', 0)} → "
        f"deduped={rec.get('post_dedupe_count', 0)} "
        f"(dropped {rec.get('dropped_duplicates', 0)}) → "
        f"cards={len(rec.get('issue_cards', []))} + "
        f"orphans={len(rec.get('observations', []))} · "
        f"appended={rec.get('appended', 0)} · "
        f"skips={len(rec.get('skips', []))} · "
        f"parse_errors={rec.get('parse_errors', 0)} · "
        f"cost_calls={rec.get('cost_calls', 0)}\n"
    )

    # ---- PICKED ----
    cards = rec.get("issue_cards", []) or []
    orphans = rec.get("observations", []) or []
    out.append(f"### PICKED · {len(cards)} card(s) + {len(orphans)} orphan(s)\n")
    if cards:
        out.append("#### Issue cards\n")
        for c in cards:
            out.append(fmt_card(c))
        out.append("")
    if orphans:
        out.append("#### Orphan observations\n")
        for o in orphans:
            out.append(fmt_obs(o))
        out.append("")
    if not cards and not orphans:
        out.append("_(nothing picked)_\n")

    # ---- DROPPED ----
    skips = rec.get("skips", []) or []
    dropped = rec.get("dropped_duplicates", 0)
    low_imp = rec.get("low_importance_dropped", 0)
    out.append(
        f"### DROPPED · {len(skips)} skip(s) · "
        f"{dropped} dedup loss(es) · "
        f"{low_imp} low-importance drop(s)\n"
    )
    if skips:
        out.append("#### Window skips\n")
        for i, s in enumerate(skips):
            out.append(fmt_skip(s, i))
        out.append("")
    if dropped:
        out.append(
            f"_Content-hash exact-duplicate check collapsed {dropped} duplicate(s); "
            "JSON does not currently retain the dropped twins._\n"
        )
    if low_imp:
        out.append(
            f"_Importance gate (< 0.3) filtered {low_imp} observation(s) "
            "before they reached the dedup pass; the LLM-emitted text is "
            "not retained for these drops._\n"
        )
    if not skips and not dropped and not low_imp:
        out.append("_(nothing dropped — all windows productive, no content-hash duplicates, no low-importance filters)_\n")

    # ---- ENRICHED ----
    affect = rec.get("affect_signals", []) or []
    affect_active = [a for a in affect if a.get("max_boost", 0) > 0]
    self_obs = rec.get("self_observations", []) or []
    synth = rec.get("synthesis") or {}
    synth_notes = synth.get("synthesis_notes", "")
    out.append(
        f"### ENRICHED · {len(affect_active)}/{len(affect)} affect-active · "
        f"synthesis={synth.get('outcome', '?')} · "
        f"self_obs={len(self_obs)}\n"
    )
    if affect_active:
        out.append("#### Affect priors that actually boosted\n")
        for i, a in enumerate(affect):
            if a.get("max_boost", 0) <= 0:
                continue
            line = fmt_affect(a, i)
            if line:
                out.append(line)
        out.append("")
    else:
        out.append(
            "_No affect signals exceeded boost threshold "
            f"(max signal in window pool: "
            f"{max((a.get('max_boost', 0) for a in affect), default=0):.2f})._\n"
        )
    if synth_notes:
        out.append("#### Synthesis notes\n")
        out.append(f"> {synth_notes}\n")
    if self_obs:
        out.append("#### Self-reflection observations\n")
        for s in self_obs:
            out.append(fmt_self_obs(s))
        out.append("")
    else:
        out.append("_No self-reflection rules fired._\n")

    out.append("---\n")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", type=Path, help="JSON report from run_selected_sessions")
    ap.add_argument("--out", type=Path, default=None, help="Output markdown path")
    args = ap.parse_args()

    data = json.loads(args.report.read_text())

    # Cross-session header
    n_sessions = len(data)
    total_cards = sum(len(r.get("issue_cards", [])) for r in data.values())
    total_orphans = sum(len(r.get("observations", [])) for r in data.values())
    total_skips = sum(len(r.get("skips", [])) for r in data.values())
    total_raw = sum(r.get("raw_count", 0) for r in data.values())
    total_dropped = sum(r.get("dropped_duplicates", 0) for r in data.values())
    total_low_imp = sum(r.get("low_importance_dropped", 0) for r in data.values())
    rule_fires: dict[str, int] = {}
    for r in data.values():
        for s in r.get("self_observations", []) or []:
            rid = s.get("rule_id", "?")
            rule_fires[rid] = rule_fires.get(rid, 0) + 1

    out = [
        f"# Pipeline audit · {n_sessions} session(s)\n",
        f"_Source: `{args.report}`_\n",
        "## Aggregate\n",
        "| metric | value |",
        "|---|---|",
        f"| sessions | {n_sessions} |",
        f"| raw observations | {total_raw} |",
        f"| Content-hash duplicates dropped | {total_dropped} |",
        f"| Low-importance (<0.3) dropped | {total_low_imp} |",
        f"| issue cards picked | {total_cards} |",
        f"| orphan observations picked | {total_orphans} |",
        f"| window skips | {total_skips} |",
        "",
        "### Self-reflection rule fires (across sessions)\n",
    ]
    if rule_fires:
        out.append("| rule_id | fires |")
        out.append("|---|---|")
        for rid, n in sorted(rule_fires.items(), key=lambda kv: -kv[1]):
            out.append(f"| `{rid}` | {n} |")
        out.append("")
    else:
        out.append("_No rules fired across the run._\n")

    out.append("\n---\n")

    for sid, rec in data.items():
        out.append(render_session(sid, rec))

    text = "\n".join(out)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
