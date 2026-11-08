"""Quick commands CRUD — backed by MooFile BSON store under db/db_quick_commands_*."""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from utils.moofile_util import MooFileUtil

logger = logging.getLogger("api.quick_commands")
router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DIR_PREFIX = "db_quick_commands_"
_DB_NAME = "quick_commands"
_INDEXES: list[str] = []


def _root_dir() -> Path:
    db_dir = _PROJECT_ROOT / "db"
    if db_dir.is_dir():
        matches = sorted(p for p in db_dir.glob(f"{_DIR_PREFIX}*") if p.is_dir())
        if matches:
            return matches[0]
    root = db_dir / f"{_DIR_PREFIX}{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": root.name,
        "name": _DB_NAME,
        "type": "normal",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "trashed": False,
    }
    with open(root / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return root


def _util() -> MooFileUtil:
    u = MooFileUtil(str(_root_dir()))
    if not u.database_exists(_DB_NAME):
        u.create_database(_DB_NAME)
    return u


class QuickCommandIn(BaseModel):
    title: str
    content: str
    agent: str = "default"
    model: str = ""
    mode: str = "task"
    autoSend: bool = False


@router.get("/api/quick-commands")
async def list_commands():
    util = _util()
    items = list(util.query_data(_DB_NAME, {}, _INDEXES))
    items.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
    return {"ok": True, "items": items}


@router.post("/api/quick-commands")
async def create_command(body: QuickCommandIn):
    util = _util()
    item = body.model_dump()
    item["id"] = "qc_" + uuid.uuid4().hex[:8]
    item["recordType"] = "record"
    item["createdAt"] = datetime.now(timezone.utc).isoformat()
    util.insert_data(_DB_NAME, [item], _INDEXES)
    return {"ok": True, "item": item}


@router.put("/api/quick-commands/{cmd_id}")
async def update_command(cmd_id: str, body: QuickCommandIn):
    util = _util()
    existing = util.query_data(_DB_NAME, {"id": cmd_id}, _INDEXES)
    if not existing:
        return {"ok": False, "error": "not found"}
    updated = {**existing[0], **body.model_dump(), "updatedAt": datetime.now(timezone.utc).isoformat()}
    util.update_data(_DB_NAME, {"id": cmd_id}, updated, _INDEXES)
    return {"ok": True, "item": updated}


@router.delete("/api/quick-commands/{cmd_id}")
async def delete_command(cmd_id: str):
    util = _util()
    util.delete_data(_DB_NAME, {"id": cmd_id}, _INDEXES)
    return {"ok": True}
