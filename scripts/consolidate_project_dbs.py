#!/usr/bin/env python3
"""
Consolidate per-project memesis databases into the global ~/.claude/memory/index.db.

Each row is stamped with a `project` column set to the Claude Code directory slug
(e.g. -Users-emmahyde-projects-memesis).

Usage:
    uv run python3 scripts/consolidate_project_dbs.py [--dry-run] [--verbose]

Strategy C for observation IDs (integer autoincrement collision):
  - Reissue new global IDs for observations.
  - Build per-source {orig_id → new_id} map.
  - Rewrite memories.linked_observation_ids FK references via map.

Skipped:
  - pytest temp dirs
  - Junk slugs: -, --, --base-dir, -tmp-test
  - Malformed DBs (disk image is malformed)
  - Rows already present in global DB by id (memories) or content_hash (observations)
"""

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

GLOBAL_DB = Path.home() / ".claude" / "memory" / "index.db"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

JUNK_SLUGS = frozenset({"-", "--", "--base-dir", "-tmp-test"})


def slugify(project_dir: Path) -> str:
    return project_dir.name


def is_junk(slug: str) -> bool:
    if slug in JUNK_SLUGS:
        return True
    if "pytest" in slug:
        return True
    return False


def open_ro(path: Path) -> sqlite3.Connection | None:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM memories LIMIT 1")
        return conn
    except sqlite3.DatabaseError as e:
        log.warning("skip %s: %s", path, e)
        return None


def get_memory_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM memories")}


def get_obs_hashes(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT content_hash FROM observations WHERE content_hash IS NOT NULL"
    )}


def memories_cols(conn: sqlite3.Connection) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(memories)")]


def obs_cols(conn: sqlite3.Connection) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(observations)")]


def rewrite_linked_obs(raw: str | None, id_map: dict[int, int]) -> str | None:
    """Rewrite memories.linked_observation_ids using the {orig → new} id map."""
    if not raw:
        return raw
    try:
        ids = json.loads(raw)
        if not isinstance(ids, list):
            return raw
        rewritten = [id_map.get(int(x), x) if str(x).isdigit() else x for x in ids]
        return json.dumps(rewritten)
    except Exception:
        return raw


def consolidate(dry_run: bool, verbose: bool) -> None:
    if not GLOBAL_DB.exists():
        log.error("Global DB not found: %s", GLOBAL_DB)
        sys.exit(1)

    global_conn = sqlite3.connect(str(GLOBAL_DB))
    global_conn.execute("PRAGMA journal_mode=wal")
    global_conn.execute("PRAGMA busy_timeout=5000")

    # Ensure project column exists (migration may not have run yet on global DB)
    for tbl in ("memories", "observations"):
        existing = [r[1] for r in global_conn.execute(f"PRAGMA table_info({tbl})")]
        if "project" not in existing:
            if not dry_run:
                global_conn.execute(f"ALTER TABLE {tbl} ADD COLUMN project TEXT")
                global_conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{tbl}_project ON {tbl}(project)"
                )
                global_conn.commit()
                log.info("Added project column to %s", tbl)
            else:
                log.info("[dry-run] Would add project column to %s", tbl)

    # Stamp existing global rows as 'global' (only if column already exists)
    global_mem_cols_now = [r[1] for r in global_conn.execute("PRAGMA table_info(memories)")]
    if "project" in global_mem_cols_now:
        existing_global = global_conn.execute(
            "SELECT COUNT(*) FROM memories WHERE project IS NULL"
        ).fetchone()[0]
        if existing_global > 0:
            if not dry_run:
                global_conn.execute("UPDATE memories SET project='global' WHERE project IS NULL")
                global_conn.commit()
                log.info("Stamped %d existing global rows with project='global'", existing_global)
            else:
                log.info("[dry-run] Would stamp %d global rows with project='global'", existing_global)
    else:
        log.info("[dry-run] Would stamp existing global rows with project='global' after column add")

    existing_mem_ids = get_memory_ids(global_conn)
    existing_obs_hashes = get_obs_hashes(global_conn)

    # Discover project DBs
    source_dbs = sorted(PROJECTS_DIR.glob("*/memory/index.db"))

    total_mem_in = total_mem_out = total_obs_in = total_obs_out = 0

    for db_path in source_dbs:
        slug = slugify(db_path.parent.parent)
        if is_junk(slug):
            log.info("skip junk: %s", slug)
            continue

        src = open_ro(db_path)
        if src is None:
            continue

        src_mem_cols = memories_cols(src)
        src_obs_cols = obs_cols(src)

        # ---- memories ----
        mem_rows = src.execute("SELECT * FROM memories").fetchall()
        total_mem_in += len(mem_rows)

        # Columns present in source; we'll build INSERT with intersection + project
        global_mem_cols = [r[1] for r in global_conn.execute("PRAGMA table_info(memories)")]
        shared_mem_cols = [c for c in src_mem_cols if c in set(global_mem_cols) and c != "project"]
        col_idx = {c: i for i, c in enumerate(src_mem_cols)}

        mem_inserted = 0
        obs_id_map: dict[int, int] = {}  # orig → new global id

        for row in mem_rows:
            row_id = row[col_idx["id"]]
            if row_id in existing_mem_ids:
                if verbose:
                    log.debug("skip existing memory %s", row_id)
                continue

            vals = {c: row[col_idx[c]] for c in shared_mem_cols}
            vals["project"] = slug

            placeholders = ", ".join(["?"] * len(vals))
            col_names = ", ".join(vals.keys())
            if not dry_run:
                global_conn.execute(
                    f"INSERT OR IGNORE INTO memories ({col_names}) VALUES ({placeholders})",
                    list(vals.values()),
                )
            existing_mem_ids.add(row_id)
            mem_inserted += 1

        # ---- observations ----
        obs_rows = src.execute("SELECT * FROM observations").fetchall()
        total_obs_in += len(obs_rows)

        global_obs_cols = [r[1] for r in global_conn.execute("PRAGMA table_info(observations)")]
        shared_obs_cols = [
            c for c in src_obs_cols
            if c in set(global_obs_cols) and c not in ("id", "project")
        ]
        obs_col_idx = {c: i for i, c in enumerate(src_obs_cols)}

        obs_inserted = 0
        for row in obs_rows:
            ch = row[obs_col_idx.get("content_hash", -1)] if "content_hash" in obs_col_idx else None
            if ch and ch in existing_obs_hashes:
                if verbose:
                    log.debug("skip dup obs hash %s", ch)
                continue

            vals = {c: row[obs_col_idx[c]] for c in shared_obs_cols}
            vals["project"] = slug

            placeholders = ", ".join(["?"] * len(vals))
            col_names = ", ".join(vals.keys())
            if not dry_run:
                cur = global_conn.execute(
                    f"INSERT INTO observations ({col_names}) VALUES ({placeholders})",
                    list(vals.values()),
                )
                new_id = cur.lastrowid
                orig_id = row[obs_col_idx["id"]]
                obs_id_map[orig_id] = new_id
                if ch:
                    existing_obs_hashes.add(ch)
            obs_inserted += 1

        # ---- rewrite linked_observation_ids in inserted memories ----
        if obs_id_map and not dry_run:
            for row in mem_rows:
                row_id = row[col_idx["id"]]
                raw_links = row[col_idx.get("linked_observation_ids", -1)] \
                    if "linked_observation_ids" in col_idx else None
                if not raw_links:
                    continue
                rewritten = rewrite_linked_obs(raw_links, obs_id_map)
                if rewritten != raw_links:
                    global_conn.execute(
                        "UPDATE memories SET linked_observation_ids=? WHERE id=?",
                        (rewritten, row[col_idx["id"]]),
                    )

        if not dry_run:
            global_conn.commit()

        total_mem_out += mem_inserted
        total_obs_out += obs_inserted

        prefix = "[dry-run] " if dry_run else ""
        log.info(
            "%s%s: mem %d→%d inserted, obs %d→%d inserted",
            prefix, slug,
            len(mem_rows), mem_inserted,
            len(obs_rows), obs_inserted,
        )

        src.close()

    global_conn.close()

    log.info(
        "\nTotals: memories %d scanned / %d inserted | observations %d scanned / %d inserted",
        total_mem_in, total_mem_out,
        total_obs_in, total_obs_out,
    )
    if dry_run:
        log.info("Dry run complete — no writes made.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print plan, no writes")
    parser.add_argument("--verbose", action="store_true", help="Log skipped rows")
    args = parser.parse_args()
    consolidate(dry_run=args.dry_run, verbose=args.verbose)


if __name__ == "__main__":
    main()
