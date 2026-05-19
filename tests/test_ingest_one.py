import sqlite3
from pathlib import Path

from scripts import ingest_one


def _init_test_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO items (id, name) VALUES (1, 'alpha')")
        conn.commit()
    finally:
        conn.close()


def test_diff_sql_detects_insert_update_delete(tmp_path):
    before_db = tmp_path / "before.db"
    after_db = tmp_path / "after.db"

    _init_test_db(before_db)
    _init_test_db(after_db)

    conn = sqlite3.connect(str(after_db))
    try:
        conn.execute("UPDATE items SET name='alpha-updated' WHERE id=1")
        conn.execute("INSERT INTO items (id, name) VALUES (2, 'beta')")
        conn.execute("DELETE FROM items WHERE id=2")
        conn.execute("INSERT INTO items (id, name) VALUES (3, 'gamma')")
        conn.commit()
    finally:
        conn.close()

    sql = ingest_one._diff_sql(before_db, after_db)

    assert any('UPDATE "items" SET "name" = \'alpha-updated\'' in s for s in sql)
    assert any('INSERT INTO "items"' in s and "gamma" in s for s in sql)


def test_ingest_with_capture_uses_shadow_copy(tmp_path, monkeypatch):
    canonical_dir = tmp_path / "canonical"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    canonical_db = canonical_dir / "index.db"
    _init_test_db(canonical_db)

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        '{"type":"message","role":"user","content":"x"}\n', encoding="utf-8"
    )

    capture_path = tmp_path / "changeset.sql"

    monkeypatch.setattr(ingest_one, "global_memory_dir", lambda: canonical_dir)

    def _fake_ingest(_jsonl_path: Path, *, base_dir: Path | None = None):
        assert base_dir is not None
        scratch_db = base_dir / "index.db"
        conn = sqlite3.connect(str(scratch_db))
        try:
            conn.execute("INSERT INTO items (id, name) VALUES (2, 'from-shadow')")
            conn.commit()
        finally:
            conn.close()
        return {
            "wallclock_s": 0.01,
            "n_windows": 1,
            "raw_obs": 1,
            "deduped_obs": 1,
            "kept": 1,
            "pruned": 0,
            "promoted": 0,
        }

    monkeypatch.setattr(ingest_one, "ingest", _fake_ingest)

    ingest_one.ingest_with_capture(transcript, capture_path)

    conn = sqlite3.connect(str(canonical_db))
    try:
        rows = conn.execute("SELECT id, name FROM items ORDER BY id").fetchall()
    finally:
        conn.close()

    assert rows == [(1, "alpha")]

    sql_text = capture_path.read_text(encoding="utf-8")
    assert "BEGIN;" in sql_text
    assert 'INSERT INTO "items"' in sql_text
    assert "from-shadow" in sql_text
    assert "COMMIT;" in sql_text
