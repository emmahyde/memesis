#!/usr/bin/env python3
"""
Read-mostly Observer sidecar for Memesis visualization.

Observer consumes these HTTP/SSE routes instead of opening the Memesis sqlite
schema directly. Memesis stays the canonical writer for lifecycle state.
"""

import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, request

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import close_db, get_db_path, init_db
from core.models import EvalRun

ROOT = Path(__file__).parent.parent
HOST = "127.0.0.1"
PORT = 4101

app = Flask(__name__)
event_clients = set()

PRIVATE_PATTERNS = [
    re.compile(r"\b(user|i)\s+(?:am|was|is|seems|feel|feels)\s+[^.]{0,80}", re.IGNORECASE),
    re.compile(r"\b(frustrated|angry|upset|excited|sad|depressed|anxious|mood)\b[^.]{0,80}", re.IGNORECASE),
]


def resolve_db_path() -> Path:
    env_path = os.environ.get("MEMESIS_DB_PATH")
    if env_path:
        return Path(env_path).expanduser()

    global_db = Path.home() / ".claude" / "memory" / "index.db"
    if global_db.exists():
        return global_db

    projects_root = Path.home() / ".claude" / "projects"
    best = None
    best_mtime = 0
    if projects_root.exists():
        for candidate in projects_root.glob("*/memory/index.db"):
            mtime = candidate.stat().st_mtime
            if mtime > best_mtime:
                best = candidate
                best_mtime = mtime

    return best or global_db


def connect_readonly():
    db_path = resolve_db_path()
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = connect_readonly()
    if conn is None:
        return []
    try:
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def one(sql: str, params: tuple = ()) -> dict | None:
    conn = connect_readonly()
    if conn is None:
        return None
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def table_exists(name: str) -> bool:
    found = one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return found is not None


def redact_private(text: str | None) -> str:
    if not text:
        return ""
    redacted = text
    for pattern in PRIVATE_PATTERNS:
        redacted = pattern.sub("[redacted private affect signal]", redacted)
    return redacted


def parse_json(value, fallback=None):
    if fallback is None:
        fallback = {}
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def status_payload(data):
    return {
        "sidecar": {
            "ok": True,
            "dbPath": str(resolve_db_path()),
            "dbExists": resolve_db_path().exists(),
        },
        **data,
    }


def publish(event_type: str, payload: dict):
    event = {"type": event_type, "ts": datetime.now().isoformat(), **payload}
    stale = []
    for client in event_clients:
        try:
            client.put_nowait(event)
        except Exception:
            stale.append(client)
    for client in stale:
        event_clients.discard(client)


@app.get("/health")
@app.get("/memesis/health")
def health():
    return jsonify(status_payload({"status": "ok"}))


@app.get("/memesis/stats")
def stats():
    stage_rows = rows(
        "SELECT stage, COUNT(*) AS count FROM memories "
        "WHERE archived_at IS NULL GROUP BY stage"
    )
    since = (datetime.now() - timedelta(hours=24)).isoformat()
    retrievals = one(
        "SELECT COUNT(*) AS count FROM retrieval_log WHERE timestamp >= ?",
        (since,),
    ) or {"count": 0}
    consolidations = one(
        "SELECT COUNT(*) AS count FROM consolidation_log WHERE timestamp >= ?",
        (since,),
    ) or {"count": 0}
    return jsonify(status_payload({
        "stages": {row["stage"]: row["count"] for row in stage_rows},
        "retrievalRate24h": retrievals["count"],
        "consolidationVelocity24h": consolidations["count"],
    }))


@app.get("/memesis/consolidations")
def consolidations():
    limit = min(int(request.args.get("limit", 50)), 200)
    action = request.args.get("action")
    where = "WHERE action = ?" if action else ""
    params = (action, limit) if action else (limit,)
    feed = rows(
        f"SELECT * FROM consolidation_log {where} ORDER BY timestamp DESC LIMIT ?",
        params,
    )
    observation_ids = sorted({
        obs_id
        for row in feed
        for obs_id in parse_json(row.get("input_observation_refs"), [])
    })
    observations = {}
    if observation_ids and table_exists("observations"):
        placeholders = ",".join("?" for _ in observation_ids)
        for row in rows(
            f"SELECT * FROM observations WHERE id IN ({placeholders})",
            tuple(observation_ids),
        ):
            row["content"] = redact_private(row.get("content"))
            row["filtered_content"] = redact_private(row.get("filtered_content"))
            observations[row["id"]] = row

    for item in feed:
        refs = parse_json(item.get("input_observation_refs"), [])
        item["observations"] = [observations[r] for r in refs if r in observations]
        item["prompt"] = redact_private(item.get("prompt"))
        item["llm_response"] = redact_private(item.get("llm_response"))

    return jsonify(status_payload({"items": feed}))


@app.get("/memesis/retrievals")
def retrievals():
    limit = min(int(request.args.get("limit", 50)), 200)
    was_used = request.args.get("was_used")
    where = "WHERE was_used = ?" if was_used in {"0", "1"} else ""
    params = (int(was_used), limit) if where else (limit,)
    items = rows(
        f"SELECT * FROM retrieval_log {where} ORDER BY timestamp DESC LIMIT ?",
        params,
    )
    return jsonify(status_payload({"items": items}))


@app.get("/memesis/retrievals/<int:retrieval_id>")
def retrieval_detail(retrieval_id: int):
    retrieval = one("SELECT * FROM retrieval_log WHERE id = ?", (retrieval_id,))
    if retrieval is None:
        return jsonify({"error": "not found"}), 404

    candidates = rows(
        "SELECT c.*, m.title, m.summary, m.stage, m.importance "
        "FROM retrieval_candidates c "
        "LEFT JOIN memories m ON m.id = c.memory_id "
        "WHERE c.retrieval_log_id = ? ORDER BY c.rank ASC",
        (retrieval_id,),
    )
    for candidate in candidates:
        candidate["waterfall"] = [
            {"label": "semantic", "value": candidate.get("semantic_score") or 0},
            {"label": "recency", "value": candidate.get("recency_score") or 0},
            {"label": "importance", "value": candidate.get("importance_score") or 0},
            {"label": "affect", "value": candidate.get("affect_score") or 0},
            {"label": "reinforcement", "value": candidate.get("reinforcement_score") or 0},
            {"label": "boost", "value": candidate.get("boost_score") or 0},
            {"label": "final", "value": candidate.get("final_score") or 0},
        ]
    return jsonify(status_payload({"retrieval": retrieval, "candidates": candidates}))


@app.get("/memesis/duplicates")
def duplicates():
    threshold = float(request.args.get("threshold", 0.82))
    items = []
    if table_exists("memory_edges"):
        edge_rows = rows(
            "SELECT e.*, a.title AS source_title, b.title AS target_title "
            "FROM memory_edges e "
            "LEFT JOIN memories a ON a.id = e.source_id "
            "LEFT JOIN memories b ON b.id = e.target_id "
            "WHERE e.weight >= ? AND e.edge_type IN ('tag_cooccurrence', 'echo', 'semantic_duplicate') "
            "ORDER BY e.weight DESC LIMIT 100",
            (threshold,),
        )
        items = edge_rows
    return jsonify(status_payload({"threshold": threshold, "items": items}))


@app.get("/memesis/lifecycle-flows")
def lifecycle_flows():
    flow_rows = rows(
        "SELECT from_stage, to_stage, action, COUNT(*) AS count "
        "FROM consolidation_log GROUP BY from_stage, to_stage, action "
        "ORDER BY count DESC"
    )
    return jsonify(status_payload({"flows": flow_rows}))


@app.get("/memesis/threads")
def threads():
    thread_rows = rows(
        "SELECT t.*, COUNT(tm.memory_id) AS member_count "
        "FROM narrative_threads t "
        "LEFT JOIN thread_members tm ON tm.thread_id = t.id "
        "GROUP BY t.id ORDER BY member_count DESC LIMIT 200"
    )
    members = rows(
        "SELECT tm.*, m.title, m.stage, m.importance "
        "FROM thread_members tm LEFT JOIN memories m ON m.id = tm.memory_id "
        "ORDER BY tm.thread_id, tm.position"
    )
    by_thread = {}
    for member in members:
        by_thread.setdefault(member["thread_id"], []).append(member)
    for thread in thread_rows:
        thread["arc_affect"] = parse_json(thread.get("arc_affect"), {})
        thread["members"] = by_thread.get(thread["id"], [])
    return jsonify(status_payload({"items": thread_rows}))


@app.get("/memesis/affect-state")
def affect_state():
    latest = one("SELECT * FROM affect_log ORDER BY timestamp DESC LIMIT 1")
    return jsonify(status_payload({"state": latest or {}}))


@app.get("/memesis/affect-log")
def affect_log():
    since = request.args.get("since")
    if since:
        items = rows(
            "SELECT * FROM affect_log WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT 1000",
            (since,),
        )
    else:
        items = rows("SELECT * FROM affect_log ORDER BY timestamp DESC LIMIT 200")
    return jsonify(status_payload({"items": items}))


@app.get("/memesis/coherence")
def coherence():
    divergent = rows(
        "SELECT id, title, summary, stage, tags FROM memories "
        "WHERE archived_at IS NULL AND tags LIKE '%coherence_divergent%' "
        "ORDER BY updated_at DESC LIMIT 100"
    )
    total = one(
        "SELECT COUNT(*) AS count FROM memories WHERE archived_at IS NULL"
    ) or {"count": 0}
    score = 1.0 - (len(divergent) / max(total["count"], 1))
    return jsonify(status_payload({"score": score, "divergent": divergent}))


@app.get("/memesis/privacy-audit")
def privacy_audit():
    items = rows(
        "SELECT id, created_at, content, filtered_content, status "
        "FROM observations ORDER BY created_at DESC LIMIT 50"
    )
    for item in items:
        item["before"] = redact_private(item.pop("content", ""))
        item["after"] = redact_private(item.pop("filtered_content", ""))
    return jsonify(status_payload({"items": items}))


@app.get("/memesis/reconsolidation-diffs")
def reconsolidation_diffs():
    items = rows(
        "SELECT * FROM consolidation_log "
        "WHERE action IN ('merged', 'subsumed', 'deprecated', 'archived') "
        "ORDER BY timestamp DESC LIMIT 100"
    )
    return jsonify(status_payload({"items": items}))


@app.get("/memesis/evals")
def evals():
    items = rows("SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT 100")
    if not items:
        report_path = ROOT / "eval" / "reports" / "latest.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            items = [{
                "run_id": "latest-json",
                "created_at": report.get("timestamp"),
                "finished_at": report.get("timestamp"),
                "suite": "report.py",
                "status": "completed",
                "score": None,
                "report_json": json.dumps(report),
            }]
    return jsonify(status_payload({"items": items}))


@app.post("/memesis/evals/run")
def run_eval():
    body = request.get_json(silent=True) or {}
    suite = body.get("suite", "report")
    mode = body.get("mode", "synthetic")
    run_id = f"eval-{uuid.uuid4().hex[:10]}"
    command = [sys.executable, "eval/report.py"]
    if mode == "synthetic":
        command.append("--synthetic-only")
    elif mode == "live":
        command.append("--live-only")

    init_db()
    EvalRun.create(
        run_id=run_id,
        suite=suite,
        status="running",
        command=" ".join(command),
        config_a=json.dumps(body.get("configA")) if body.get("configA") else None,
        config_b=json.dumps(body.get("configB")) if body.get("configB") else None,
    )
    close_db()

    def worker():
        status = "completed"
        report = {}
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            report_path = ROOT / "eval" / "reports" / "latest.json"
            if report_path.exists():
                report = json.loads(report_path.read_text())
            report["stdout"] = completed.stdout[-6000:]
            report["stderr"] = completed.stderr[-6000:]
            if completed.returncode != 0:
                status = "failed"
        except Exception as exc:
            status = "failed"
            report = {"error": str(exc)}

        init_db()
        try:
            EvalRun.update(
                status=status,
                finished_at=datetime.now().isoformat(),
                report_json=json.dumps(report),
            ).where(EvalRun.run_id == run_id).execute()
        finally:
            close_db()
        publish("eval", {"runId": run_id, "status": status})

    threading.Thread(target=worker, daemon=True).start()
    publish("eval", {"runId": run_id, "status": "running"})
    return jsonify(status_payload({"runId": run_id, "status": "running"}))


@app.get("/memesis/events")
def events():
    client = queue.Queue(maxsize=100)
    event_clients.add(client)

    def stream():
        try:
            yield "event: memesis_event\ndata: {\"type\":\"connected\"}\n\n"
            while True:
                event = client.get()
                yield f"event: memesis_event\ndata: {json.dumps(event)}\n\n"
        finally:
            event_clients.discard(client)

    return Response(stream(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
