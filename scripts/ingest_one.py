#!/usr/bin/env python3
"""Ingest a single transcript JSONL through the full memory pipeline.

Usage:
    python3 scripts/ingest_one.py <path/to/session.jsonl>
    python3 scripts/ingest_one.py <path/to/session.jsonl> --capture changeset.sql

Bypasses the cursor (always reads the full file from offset 0). Runs:
  1. extract_observations
  2. append_to_ephemeral
  3. Consolidator.consolidate_session  (rc++ on matched memories)
  4. Crystallizer.crystallize_candidates  (promote rc>=3 memories)

`--capture` runs ingest against a shadow copy of the global DB and writes the
net DB mutation as replayable SQL without touching the canonical store.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.crystallizer import Crystallizer
from core.database import close_db, init_db
from core.lifecycle import LifecycleManager
from core.models import Memory
from core.session_detector import detect_session_type
from core.trace import TraceWriter, set_active_writer
from core.transcript import extract_tool_uses, read_transcript_from
from core.transcript_ingest import (
    append_to_ephemeral,
    extract_observations_hierarchical,
    global_memory_dir,
    transcript_project_slug,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ingest_one] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _stage_counts() -> dict[str, int]:
    return {
        s: Memory.select()
        .where(Memory.stage == s, Memory.archived_at.is_null())
        .count()
        for s in ("ephemeral", "consolidated", "crystallized", "instinctive")
    }


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return f"X'{value.hex()}'"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _list_user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
    return [str(r[1]) for r in rows]


def _table_pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
    pks = sorted((r for r in rows if int(r[5]) > 0), key=lambda r: int(r[5]))
    return [str(r[1]) for r in pks]


def _sort_key(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _load_rows(
    conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    pk_columns: list[str],
) -> dict[tuple[object, ...], tuple[object, ...]]:
    col_select = ", ".join(_quote_ident(c) for c in columns)
    rows = conn.execute(f"SELECT {col_select} FROM {_quote_ident(table)}").fetchall()
    if pk_columns:
        key_idx = [columns.index(c) for c in pk_columns]
        return {tuple(row[i] for i in key_idx): tuple(row) for row in rows}

    out: dict[tuple[object, ...], tuple[object, ...]] = {}
    for idx, row in enumerate(rows):
        out[(idx,)] = tuple(row)
    return out


def _diff_sql(before_db: Path, after_db: Path) -> list[str]:
    before = sqlite3.connect(str(before_db))
    after = sqlite3.connect(str(after_db))
    before.row_factory = sqlite3.Row
    after.row_factory = sqlite3.Row

    try:
        statements: list[str] = []
        tables = sorted(set(_list_user_tables(before)) | set(_list_user_tables(after)))

        for table in tables:
            if table not in set(_list_user_tables(before)):
                continue
            if table not in set(_list_user_tables(after)):
                continue

            columns = _table_columns(after, table)
            if not columns:
                continue
            pk_columns = _table_pk_columns(after, table)

            before_rows = _load_rows(before, table, columns, pk_columns)
            after_rows = _load_rows(after, table, columns, pk_columns)

            key_cols = pk_columns if pk_columns else [columns[0]]
            key_idx = [columns.index(c) for c in key_cols]

            for key in sorted(set(before_rows) - set(after_rows), key=_sort_key):
                row = before_rows[key]
                where = " AND ".join(
                    f"{_quote_ident(c)} = {_sql_literal(row[i])}"
                    for c, i in zip(key_cols, key_idx)
                )
                statements.append(f"DELETE FROM {_quote_ident(table)} WHERE {where};")

            for key in sorted(set(after_rows) - set(before_rows), key=_sort_key):
                row = after_rows[key]
                cols = ", ".join(_quote_ident(c) for c in columns)
                vals = ", ".join(_sql_literal(v) for v in row)
                statements.append(
                    f"INSERT INTO {_quote_ident(table)} ({cols}) VALUES ({vals});"
                )

            for key in sorted(set(before_rows) & set(after_rows), key=_sort_key):
                b = before_rows[key]
                a = after_rows[key]
                if b == a:
                    continue
                set_parts = [
                    f"{_quote_ident(col)} = {_sql_literal(a[idx])}"
                    for idx, col in enumerate(columns)
                    if a[idx] != b[idx]
                ]
                where = " AND ".join(
                    f"{_quote_ident(c)} = {_sql_literal(a[i])}"
                    for c, i in zip(key_cols, key_idx)
                )
                statements.append(
                    f"UPDATE {_quote_ident(table)} SET {', '.join(set_parts)} WHERE {where};"
                )

        return statements
    finally:
        before.close()
        after.close()


def _append_benchmark(record: dict[str, object]) -> None:
    out = Path.home() / ".claude" / "memesis" / "benchmarks" / "ingest-tokens.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _sum_llm_trace_tokens(trace_file: Path) -> tuple[int, int, int]:
    tokens_in = 0
    tokens_out = 0
    cache_read = 0
    if not trace_file.exists():
        return tokens_in, tokens_out, cache_read

    with trace_file.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("stage") != "llm" or rec.get("event") != "llm_envelope":
                continue
            payload = rec.get("payload") or {}
            tokens_in += int(payload.get("input_tokens") or 0)
            tokens_out += int(payload.get("output_tokens") or 0)
            cache_read += int(payload.get("cache_read_input_tokens") or 0)

    return tokens_in, tokens_out, cache_read


def _copy_db_with_sidecars(src_db: Path, dest_db: Path) -> None:
    shutil.copy2(src_db, dest_db)
    for suffix in ("-wal", "-shm"):
        src_side = Path(str(src_db) + suffix)
        if src_side.exists():
            shutil.copy2(src_side, Path(str(dest_db) + suffix))


def ingest(jsonl_path: Path, *, base_dir: Path | None = None) -> dict[str, object]:
    if not jsonl_path.exists():
        sys.exit(f"transcript not found: {jsonl_path}")

    started = time.perf_counter()
    stats: dict[str, object] = {
        "wallclock_s": 0.0,
        "n_windows": 0,
        "raw_obs": 0,
        "deduped_obs": 0,
        "kept": 0,
        "pruned": 0,
        "promoted": 0,
    }

    project_slug = transcript_project_slug(jsonl_path)
    mem_dir = base_dir if base_dir is not None else global_memory_dir()
    init_db(project=project_slug, base_dir=str(mem_dir))

    trace_id = f"ingest-{jsonl_path.stem}-{int(time.time())}"
    trace_dir = mem_dir / ".tmp-ingest-traces"
    writer = TraceWriter(trace_id, base_dir=trace_dir)
    set_active_writer(writer)

    try:
        before = _stage_counts()
        logger.info("Stage counts before: %s", before)

        entries, _, transcript_cwd = read_transcript_from(jsonl_path, 0)
        if not entries:
            logger.warning("transcript empty: %s", jsonl_path)
            stats = {
                "wallclock_s": round(time.perf_counter() - started, 3),
                "n_windows": 0,
                "raw_obs": 0,
                "deduped_obs": 0,
                "kept": 0,
                "pruned": 0,
                "promoted": 0,
            }
            return stats

        session_cwd = transcript_cwd
        tool_uses = extract_tool_uses(jsonl_path)

        session_type = detect_session_type(session_cwd, tool_uses or None)
        logger.info(
            "Session type: %s | %d entries | %d tool_uses",
            session_type,
            len(entries),
            len(tool_uses),
        )

        total_chars = sum(len(e.get("text", "")) for e in entries)
        n_windows = max(10, total_chars // 12800 + 2)
        extract_result = extract_observations_hierarchical(
            entries,
            session_type=session_type,
            max_windows=n_windows,
            cwd=session_cwd,
        )
        obs_list = extract_result["observations"]
        logger.info(
            "Hierarchical extraction: %d window(s), raw=%d → %d deduped observation(s)",
            extract_result["windows"],
            extract_result.get("raw_count", 0),
            len(obs_list),
        )
        for obs in obs_list:
            obs.setdefault("session_type", session_type)

        eph_path = (
            mem_dir
            / "ephemeral"
            / project_slug
            / f"session-{date.today().isoformat()}.md"
        )
        if eph_path.exists():
            eph_path.unlink()
        n_appended = append_to_ephemeral(
            mem_dir, obs_list, dry_run=False, project_slug=project_slug
        )
        logger.info(
            "Extracted %d observations, appended %d to ephemeral",
            len(obs_list),
            n_appended,
        )

        lifecycle = LifecycleManager()
        consolidation_result: dict[str, list] = {
            "kept": [],
            "pruned": [],
            "promoted": [],
            "conflicts": [],
        }
        if not eph_path.exists():
            logger.warning("No ephemeral file at %s — nothing to consolidate", eph_path)
        else:
            consolidator = Consolidator(lifecycle=lifecycle)
            consolidation_result = consolidator.consolidate_session(
                str(eph_path), session_id=jsonl_path.stem
            )
            logger.info(
                "Consolidation: kept=%d pruned=%d promoted=%d conflicts=%d",
                len(consolidation_result.get("kept", [])),
                len(consolidation_result.get("pruned", [])),
                len(consolidation_result.get("promoted", [])),
                len(consolidation_result.get("conflicts", [])),
            )

        candidates = lifecycle.get_promotion_candidates()
        logger.info("Promotion candidates (rc>=3, spacing met): %d", len(candidates))
        crystallized = []
        if candidates:
            crystallizer = Crystallizer(lifecycle)
            crystallized = crystallizer.crystallize_candidates()
            for c in crystallized:
                logger.info(
                    "CRYSTALLIZED: %s (group=%d)",
                    c.get("title", "?")[:80],
                    c.get("group_size", 0),
                )

        after = _stage_counts()
        logger.info("Stage counts after:  %s", after)
        logger.info("Delta: %s", {k: after[k] - before[k] for k in after})

        if crystallized:
            print(f"\n>>> {len(crystallized)} memory(ies) crystallized this run <<<")

        wallclock_s = round(time.perf_counter() - started, 3)
        stats = {
            "wallclock_s": wallclock_s,
            "n_windows": int(extract_result.get("windows", 0)),
            "raw_obs": int(extract_result.get("raw_count", 0)),
            "deduped_obs": len(obs_list),
            "kept": len(consolidation_result.get("kept", [])),
            "pruned": len(consolidation_result.get("pruned", [])),
            "promoted": len(consolidation_result.get("promoted", [])),
        }
        return stats
    finally:
        writer.close()
        set_active_writer(None)
        close_db()

        tokens_in, tokens_out, cache_read = _sum_llm_trace_tokens(
            trace_dir / f"{trace_id}.jsonl"
        )
        _append_benchmark(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "transcript": jsonl_path.stem,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cache_read_input_tokens": cache_read,
                "wallclock_s": stats["wallclock_s"],
                "n_windows": stats["n_windows"],
                "raw_obs": stats["raw_obs"],
                "deduped_obs": stats["deduped_obs"],
                "kept": stats["kept"],
                "pruned": stats["pruned"],
                "promoted": stats["promoted"],
            }
        )


def ingest_with_capture(jsonl_path: Path, capture_path: Path) -> None:
    canonical_dir = global_memory_dir()
    canonical_db = canonical_dir / "index.db"
    if not canonical_db.exists():
        sys.exit(f"canonical db missing: {canonical_db}")

    with tempfile.TemporaryDirectory(prefix="memesis-ingest-capture-") as tmp:
        tmp_dir = Path(tmp)
        before_db = tmp_dir / "before.db"
        scratch_dir = tmp_dir / "scratch"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        scratch_db = scratch_dir / "index.db"

        _copy_db_with_sidecars(canonical_db, before_db)
        _copy_db_with_sidecars(canonical_db, scratch_db)

        ingest(jsonl_path, base_dir=scratch_dir)

        statements = _diff_sql(before_db, scratch_db)
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        with capture_path.open("w", encoding="utf-8") as fh:
            fh.write("BEGIN;\n")
            for stmt in statements:
                fh.write(stmt + "\n")
            fh.write("COMMIT;\n")

        logger.info(
            "Capture complete: %d SQL statements → %s", len(statements), capture_path
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest one transcript into memesis")
    parser.add_argument("transcript", type=str, help="Path to transcript JSONL")
    parser.add_argument(
        "--capture",
        type=str,
        default=None,
        help="Write a replayable SQL changeset instead of mutating the canonical DB",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    transcript = Path(args.transcript).expanduser().resolve()
    if args.capture:
        ingest_with_capture(transcript, Path(args.capture).expanduser().resolve())
    else:
        ingest(transcript)
