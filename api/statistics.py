from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from utils.statistics_store import (
    iter_records,
    list_records,
    options,
    statistics_enabled,
    summary,
)


router = APIRouter(prefix="/api/statistics", tags=["statistics"])


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _filters(
    from_time: datetime | None,
    to_time: datetime | None,
    machine_name: str | None,
    user_name: str | None,
    mode: str | None,
    agent_name: str | None,
    role_name: str | None,
    status: str | None,
    model_name: str | None,
    keyword: str | None,
) -> dict[str, Any]:
    if mode and mode not in {"chat", "task"}:
        raise HTTPException(status_code=422, detail="mode must be chat or task")
    return {
        "from_time": _iso(from_time),
        "to_time": _iso(to_time),
        "machine_name": machine_name,
        "user_name": user_name,
        "mode": mode,
        "agent_name": agent_name,
        "role_name": role_name,
        "status": status,
        "model_name": model_name,
        "keyword": keyword,
        "source": "interactive",
    }


def _ensure_enabled() -> None:
    if not statistics_enabled():
        raise HTTPException(status_code=503, detail="statistics is disabled")


@router.get("/records")
def get_records(
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    machine_name: str | None = Query(None, alias="machineName"),
    user_name: str | None = Query(None, alias="userName"),
    mode: str | None = None,
    agent_name: str | None = Query(None, alias="agentName"),
    role_name: str | None = Query(None, alias="roleName"),
    status: str | None = None,
    model_name: str | None = Query(None, alias="modelName"),
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, alias="pageSize", ge=1, le=500),
) -> dict[str, Any]:
    _ensure_enabled()
    filters = _filters(
        from_time,
        to_time,
        machine_name,
        user_name,
        mode,
        agent_name,
        role_name,
        status,
        model_name,
        keyword,
    )
    return list_records(filters, page=page, page_size=page_size)


@router.get("/summary")
def get_summary(
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    machine_name: str | None = Query(None, alias="machineName"),
    user_name: str | None = Query(None, alias="userName"),
    mode: str | None = None,
    agent_name: str | None = Query(None, alias="agentName"),
    role_name: str | None = Query(None, alias="roleName"),
    status: str | None = None,
    model_name: str | None = Query(None, alias="modelName"),
    keyword: str | None = None,
) -> dict[str, Any]:
    _ensure_enabled()
    return summary(
        _filters(
            from_time,
            to_time,
            machine_name,
            user_name,
            mode,
            agent_name,
            role_name,
            status,
            model_name,
            keyword,
        )
    )


@router.get("/options")
def get_options() -> dict[str, list[str]]:
    _ensure_enabled()
    return options()


def _csv_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


@router.get("/export.csv")
def export_csv(
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    machine_name: str | None = Query(None, alias="machineName"),
    user_name: str | None = Query(None, alias="userName"),
    mode: str | None = None,
    agent_name: str | None = Query(None, alias="agentName"),
    role_name: str | None = Query(None, alias="roleName"),
    status: str | None = None,
    model_name: str | None = Query(None, alias="modelName"),
    keyword: str | None = None,
) -> Response:
    _ensure_enabled()
    rows = iter_records(
        _filters(
            from_time,
            to_time,
            machine_name,
            user_name,
            mode,
            agent_name,
            role_name,
            status,
            model_name,
            keyword,
        )
    )
    columns = [
        "started_at",
        "machine_name",
        "user_name",
        "mode",
        "agent_name",
        "role_name",
        "model_name",
        "tool_call_count",
        "ai_call_count",
        "duration_ms",
        "status",
        "question",
        "session_id",
        "conversation_id",
        "is_retry",
    ]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_cell(row.get(column, "")) for column in columns])
    content = "\ufeff" + output.getvalue()
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="usage-statistics.csv"'
        },
    )
