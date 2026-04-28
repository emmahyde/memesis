"""
W5 schema migration: back-derive kind/subject/knowledge_type from legacy fields.

Usage:
    python scripts/migrate_w5_schema.py [--commit] [--db PATH]

Default: dry-run (no writes). Pass --commit to apply changes.

Writes audit JSONL to backfill-output/observability/w5-migration.jsonl.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.models import db
from core.session_detector import detect_session_type

# ---------------------------------------------------------------------------
# Back-derivation maps
# ---------------------------------------------------------------------------

# Stage 2 observation_type → (kind, subject, knowledge_type, work_event, flag_for_review)
# flag_for_review=True means subject is ambiguous; set knowledge_type_confidence=low
OBSERVATION_TYPE_MAP: dict[str, tuple[str, str | None, str, str | None, bool]] = {
    "correction":           ("correction",  None,            "metacognitive", None,     True),   # subject ambiguous
    "preference_signal":    ("preference",  "user",          "metacognitive", None,     False),
    "shared_insight":       ("finding",     "domain",        "conceptual",    None,     False),
    "domain_knowledge":     ("finding",     "domain",        "factual",       None,     False),   # factual default; flag if conceptual needed
    "workflow_pattern":     ("preference",  "workflow",      "procedural",    None,     False),
    "self_observation":     ("finding",     "self",          "metacognitive", None,     False),
    "decision_context":     ("decision",    None,            "conceptual",    None,     True),    # subject ambiguous
    "personality":          ("finding",     "user",          "metacognitive", None,     False),
    "aesthetic":            ("preference",  "user",          "metacognitive", None,     False),
    "collaboration_dynamic":("finding",     "collaboration", "metacognitive", None,     False),
    "system_change":        ("finding",     "system",        "factual",       "change", False),
}

# concept_tags → knowledge_type
# None means ambiguous — flag for re-classification
CONCEPT_TAGS_COLLAPSE: dict[str, str | None] = {
    "how-it-works":    "conceptual",
    "why-it-exists":   "conceptual",
    "what-changed":    "factual",
    "problem-solution": None,   # ambiguous: procedural | factual
    "gotcha":          "metacognitive",
    "pattern":         None,    # ambiguous: conceptual | procedural
    "trade-off":       "metacognitive",
}

# ---------------------------------------------------------------------------
# Audit writer
# ---------------------------------------------------------------------------

_AUDIT_PATH = Path("backfill-output") / "observability" / "w5-migration.jsonl"


def _write_audit(record: dict) -> None:
    _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Derivation helpers
# ---------------------------------------------------------------------------


def derive_from_observation_type(
    observation_type: str,
) -> dict:
    """Return a partial update dict from observation_type back-derivation."""
    entry = OBSERVATION_TYPE_MAP.get(observation_type)
    if entry is None:
        return {"_unknown_observation_type": observation_type}

    kind, subject, knowledge_type, work_event, flag = entry
    result: dict = {
        "kind": kind,
        "knowledge_type": knowledge_type,
        "knowledge_type_confidence": "low" if flag else "high",
    }
    if subject is not None:
        result["subject"] = subject
    if work_event is not None:
        result["work_event"] = work_event
    if flag:
        result["_flag_for_review"] = True
    return result


def derive_from_concept_tags(concept_tags_json: str | None) -> dict:
    """Return a partial update dict from concept_tags back-derivation."""
    if not concept_tags_json:
        return {}
    try:
        tags = json.loads(concept_tags_json)
    except (ValueError, TypeError):
        return {}
    if not isinstance(tags, list) or not tags:
        return {}

    # Use first recognisable tag
    for tag in tags:
        mapped = CONCEPT_TAGS_COLLAPSE.get(tag)
        if mapped is not None:
            return {"knowledge_type": mapped, "knowledge_type_confidence": "high"}
        if tag in CONCEPT_TAGS_COLLAPSE:
            # Ambiguous mapping
            return {"knowledge_type": None, "knowledge_type_confidence": "low", "_flag_for_review": True}

    return {}


def derive_kind_from_mode(mode: str | None) -> str | None:
    """Map legacy mode → kind (1:1)."""
    valid = {"decision", "finding", "preference", "constraint", "correction", "open_question"}
    if mode in valid:
        return mode
    return None


# ---------------------------------------------------------------------------
# Main migration
# ---------------------------------------------------------------------------


def _table_columns(table: str) -> set[str]:
    cursor = db.execute_sql(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def run_migration(commit: bool, db_path: str | None) -> dict:
    """Run migration; return stats dict."""
    if db_path:
        init_db(base_dir=str(Path(db_path).parent))
    else:
        init_db()

    # WS-H: ensure open_question lifecycle columns exist (idempotent)
    existing_cols = _table_columns("memories")
    for col, typ in [
        ("resolves_question_id", "TEXT"),
        ("resolved_at", "TEXT"),
        ("is_pinned", "INTEGER DEFAULT 0"),
    ]:
        if col not in existing_cols:
            try:
                db.execute_sql(f"ALTER TABLE memories ADD COLUMN {col} {typ}")
                print(f"  [migration] Added column: memories.{col}")
            except Exception as exc:
                print(f"  [migration] Could not add {col}: {exc}")

    # Only select columns that actually exist (legacy DBs may not have mode/observation_type/concept_tags)
    existing = _table_columns("memories")
    select_cols = ["id", "kind", "knowledge_type", "subject", "work_event"]
    for optional in ("mode", "observation_type", "concept_tags", "cwd", "session_type"):
        if optional in existing:
            select_cols.append(optional)

    cursor = db.execute_sql(f"SELECT {', '.join(select_cols)} FROM memories")
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, row)) for row in cursor.fetchall()]

    stats = {
        "rows_processed": len(rows),
        "rows_back_derived": 0,
        "rows_flagged": 0,
        "rows_skipped": 0,
        "rows_session_type_derived": 0,
        "dry_run": not commit,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    for row in rows:
        row_id = row["id"]
        updates: dict = {}
        flag = False

        # 1. mode → kind (only if kind not already set)
        if not row.get("kind"):
            derived_kind = derive_kind_from_mode(row.get("mode"))
            if derived_kind:
                updates["kind"] = derived_kind

        # 2. observation_type → (kind, subject, knowledge_type, work_event)
        obs_type = row.get("observation_type")
        if obs_type and not row.get("kind"):
            derived = derive_from_observation_type(obs_type)
            flag = flag or derived.pop("_flag_for_review", False)
            derived.pop("_unknown_observation_type", None)
            # Only set fields not already populated
            for k, v in derived.items():
                if not row.get(k):
                    updates[k] = v

        # 3. concept_tags → knowledge_type (only if knowledge_type not already set)
        if not row.get("knowledge_type") and not updates.get("knowledge_type"):
            ct_derived = derive_from_concept_tags(row.get("concept_tags"))
            flag = flag or ct_derived.pop("_flag_for_review", False)
            for k, v in ct_derived.items():
                updates[k] = v

        # 4. session_type back-derivation from cwd (Sprint B WS-G / LLME-F9)
        #    Only populate if column exists and row has no value yet.
        if "session_type" in existing and not row.get("session_type"):
            derived_session_type = detect_session_type(row.get("cwd"), default="code")
            updates["session_type"] = derived_session_type
            stats["rows_session_type_derived"] += 1

        if not updates:
            stats["rows_skipped"] += 1
            continue

        stats["rows_back_derived"] += 1
        if flag:
            stats["rows_flagged"] += 1

        audit_record = {
            "id": row_id,
            "updates": {k: v for k, v in updates.items() if not k.startswith("_")},
            "flag_for_review": flag,
            "dry_run": not commit,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        _write_audit(audit_record)

        if commit:
            set_clauses = ", ".join(f"{k} = ?" for k in updates if not k.startswith("_"))
            values = [v for k, v in updates.items() if not k.startswith("_")]
            if set_clauses:
                db.execute_sql(
                    f"UPDATE memories SET {set_clauses} WHERE id = ?",
                    values + [row_id],
                )

    close_db()
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="W5 schema back-derivation migration")
    parser.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Actually write updates (default: dry-run)",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="Path to index.db (default: ~/.claude/memory/index.db)",
    )
    args = parser.parse_args()

    stats = run_migration(commit=args.commit, db_path=args.db)

    mode_label = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[{mode_label}] W5 migration complete")
    print(f"  rows processed:    {stats['rows_processed']}")
    print(f"  rows back-derived: {stats['rows_back_derived']}")
    print(f"  rows flagged:      {stats['rows_flagged']}")
    print(f"  rows skipped:      {stats['rows_skipped']}")
    print(f"  session_type derived: {stats['rows_session_type_derived']}")
    print(f"  audit log:         {_AUDIT_PATH}")


if __name__ == "__main__":
    main()
