from __future__ import annotations

import math
import os
import platform
import socket
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY: set[Path] = set()


def statistics_enabled() -> bool:
    return os.getenv("STATISTICS_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def database_path() -> Path:
    raw = os.getenv("STATISTICS_DB_PATH", "db/statistics.sqlite3").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


def machine_name() -> str:
    return (
        os.getenv("STATISTICS_MACHINE_NAME", "").strip()
        or os.getenv("COMPUTERNAME", "").strip()
        or platform.node().strip()
        or socket.gethostname().strip()
        or "unknown"
    )


def user_name() -> str:
    domain = os.getenv("USERDOMAIN", "").strip()
    username = os.getenv("USERNAME", "").strip() or "unknown"
    return f"{domain}\\{username}" if domain else username


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA synchronous = NORMAL")
        _ensure_schema(conn, path)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection, path: Path) -> None:
    if path in _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if path in _SCHEMA_READY:
            return
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_statistics (
                id                  TEXT PRIMARY KEY,
                started_at          TEXT NOT NULL,
                finished_at         TEXT,
                machine_name        TEXT NOT NULL,
                user_name           TEXT,
                mode                TEXT NOT NULL CHECK (mode IN ('chat', 'task')),
                agent_name          TEXT NOT NULL,
                role_name           TEXT,
                model_name          TEXT,
                tool_call_count     INTEGER NOT NULL DEFAULT 0,
                ai_call_count       INTEGER NOT NULL DEFAULT 0,
                duration_ms         INTEGER,
                question            TEXT NOT NULL,
                status              TEXT NOT NULL,
                error_type          TEXT,
                session_id          TEXT NOT NULL,
                conversation_id     TEXT,
                source              TEXT NOT NULL DEFAULT 'interactive',
                is_retry            INTEGER NOT NULL DEFAULT 0,
                created_at          TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_statistics_started_at
                ON execution_statistics(started_at);
            CREATE INDEX IF NOT EXISTS idx_statistics_machine_time
                ON execution_statistics(machine_name, started_at);
            CREATE INDEX IF NOT EXISTS idx_statistics_mode_time
                ON execution_statistics(mode, started_at);
            CREATE INDEX IF NOT EXISTS idx_statistics_agent_time
                ON execution_statistics(agent_name, started_at);
            CREATE INDEX IF NOT EXISTS idx_statistics_status
                ON execution_statistics(status);
            """
        )
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(execution_statistics)"
            ).fetchall()
        }
        if "user_name" not in columns:
            conn.execute(
                "ALTER TABLE execution_statistics ADD COLUMN user_name TEXT"
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_statistics_user_time
            ON execution_statistics(user_name, started_at)
            """
        )
        conn.commit()
        _SCHEMA_READY.add(path)


def initialize() -> None:
    if not statistics_enabled():
        return
    with _connect():
        pass


def start_record(
    *,
    session_id: str,
    mode: str,
    agent_name: str,
    role_name: str,
    model_name: str,
    question: str,
    conversation_id: str | None,
    is_retry: bool,
    source: str = "interactive",
) -> tuple[str, str]:
    if mode not in {"chat", "task"}:
        raise ValueError(f"unsupported statistics mode: {mode}")
    record_id = f"turn-{uuid.uuid4().hex}"
    started_at = _now()
    store_question = os.getenv("STATISTICS_STORE_QUESTION", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO execution_statistics (
                id, started_at, machine_name, user_name, mode, agent_name, role_name,
                model_name, question, status, session_id, conversation_id,
                source, is_retry, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                started_at,
                machine_name(),
                user_name(),
                mode,
                agent_name,
                role_name or None,
                model_name or None,
                question if store_question else "",
                session_id,
                conversation_id or None,
                source,
                int(is_retry),
                started_at,
            ),
        )
    return record_id, started_at


def finish_record(
    record_id: str,
    *,
    status: str,
    duration_ms: int,
    ai_call_count: int,
    tool_call_count: int,
    error_type: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE execution_statistics
            SET finished_at = ?, status = ?, duration_ms = ?,
                ai_call_count = ?, tool_call_count = ?, error_type = ?
            WHERE id = ?
            """,
            (
                _now(),
                status,
                max(0, duration_ms),
                max(0, ai_call_count),
                max(0, tool_call_count),
                error_type,
                record_id,
            ),
        )


def mark_running_interrupted() -> int:
    if not statistics_enabled():
        return 0
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE execution_statistics
            SET finished_at = ?, status = 'interrupted',
                error_type = COALESCE(error_type, 'process_interrupted'),
                duration_ms = COALESCE(
                    duration_ms,
                    CAST((julianday(?) - julianday(started_at)) * 86400000 AS INTEGER)
                )
            WHERE status = 'running'
            """,
            (_now(), _now()),
        )
        return cursor.rowcount


def apply_retention() -> int:
    raw = os.getenv("STATISTICS_RETENTION_DAYS", "365").strip()
    try:
        days = int(raw)
    except ValueError:
        days = 365
    if days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM execution_statistics WHERE started_at < ?", (cutoff,)
        )
        return cursor.rowcount


def _where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    mappings = {
        "machine_name": "machine_name",
        "user_name": "user_name",
        "mode": "mode",
        "agent_name": "agent_name",
        "role_name": "role_name",
        "status": "status",
        "model_name": "model_name",
        "source": "source",
    }
    if filters.get("from_time"):
        clauses.append("started_at >= ?")
        params.append(filters["from_time"])
    if filters.get("to_time"):
        clauses.append("started_at <= ?")
        params.append(filters["to_time"])
    for key, column in mappings.items():
        value = filters.get(key)
        if value not in (None, ""):
            clauses.append(f"{column} = ?")
            params.append(value)
    if filters.get("keyword"):
        clauses.append("question LIKE ? ESCAPE '\\'")
        escaped = (
            str(filters["keyword"])
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        params.append(f"%{escaped}%")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def list_records(
    filters: dict[str, Any],
    *,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(500, max(1, page_size))
    where, params = _where(filters)
    with _connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM execution_statistics{where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT * FROM execution_statistics
            {where}
            ORDER BY started_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


def iter_records(filters: dict[str, Any]) -> Iterable[dict[str, Any]]:
    where, params = _where(filters)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM execution_statistics
            {where}
            ORDER BY started_at DESC
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def summary(filters: dict[str, Any]) -> dict[str, Any]:
    where, params = _where(filters)
    with _connect() as conn:
        aggregate = conn.execute(
            f"""
            SELECT
                COUNT(*) AS request_count,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
                SUM(CASE WHEN mode = 'chat' THEN 1 ELSE 0 END) AS chat_count,
                SUM(CASE WHEN mode = 'task' THEN 1 ELSE 0 END) AS task_count,
                COALESCE(SUM(tool_call_count), 0) AS tool_call_count,
                COALESCE(SUM(ai_call_count), 0) AS ai_call_count,
                COALESCE(AVG(duration_ms), 0) AS average_duration_ms
            FROM execution_statistics
            {where}
            """,
            params,
        ).fetchone()
        durations = [
            row[0]
            for row in conn.execute(
                f"""
                SELECT duration_ms FROM execution_statistics
                {where}{' AND' if where else ' WHERE'} duration_ms IS NOT NULL
                ORDER BY duration_ms
                """,
                params,
            ).fetchall()
        ]
        trend_rows = conn.execute(
            f"""
            SELECT
                substr(started_at, 1, 10) AS day,
                COUNT(*) AS request_count,
                COALESCE(SUM(ai_call_count), 0) AS ai_call_count,
                COALESCE(SUM(tool_call_count), 0) AS tool_call_count
            FROM execution_statistics
            {where}
            GROUP BY substr(started_at, 1, 10)
            ORDER BY day
            """,
            params,
        ).fetchall()
    data = dict(aggregate)
    p95_index = max(0, math.ceil(len(durations) * 0.95) - 1)
    request_count = int(data["request_count"] or 0)
    return {
        "requestCount": request_count,
        "completedCount": int(data["completed_count"] or 0),
        "failedCount": int(data["failed_count"] or 0),
        "cancelledCount": int(data["cancelled_count"] or 0),
        "chatCount": int(data["chat_count"] or 0),
        "taskCount": int(data["task_count"] or 0),
        "toolCallCount": int(data["tool_call_count"] or 0),
        "aiCallCount": int(data["ai_call_count"] or 0),
        "averageDurationMs": round(float(data["average_duration_ms"] or 0)),
        "p95DurationMs": int(durations[p95_index]) if durations else 0,
        "failureRate": (
            round(int(data["failed_count"] or 0) / request_count, 4)
            if request_count
            else 0
        ),
        "trend": [
            {
                "day": row["day"],
                "requestCount": row["request_count"],
                "aiCallCount": row["ai_call_count"],
                "toolCallCount": row["tool_call_count"],
            }
            for row in trend_rows
        ],
    }


def options() -> dict[str, list[str]]:
    columns = {
        "machineNames": "machine_name",
        "userNames": "user_name",
        "agentNames": "agent_name",
        "roleNames": "role_name",
        "modelNames": "model_name",
        "statuses": "status",
    }
    result: dict[str, list[str]] = {}
    with _connect() as conn:
        for key, column in columns.items():
            rows = conn.execute(
                f"""
                SELECT DISTINCT {column}
                FROM execution_statistics
                WHERE {column} IS NOT NULL AND {column} != ''
                ORDER BY {column}
                """
            ).fetchall()
            result[key] = [str(row[0]) for row in rows]
    return result
