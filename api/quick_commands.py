"""Quick commands CRUD — backed by a JSON file at .deepagents/quick_commands.json.

Stored as a plain JSON array so users can share/edit the file alongside the
repo (it travels with .deepagents/ instead of living in the BSON db/ dir).
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("api.quick_commands")
router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_FILE = _PROJECT_ROOT / ".deepagents" / "quick_commands.json"


def _load() -> list[dict]:
    if not _FILE.exists():
        return []
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        logger.exception("quick_commands: failed to read %s", _FILE)
        return []


def _save(items: list[dict]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


class QuickCommandIn(BaseModel):
    title: str
    content: str
    agent: str = "default"
    model: str = ""
    mode: str = "task"
    autoSend: bool = False
    type: str = "prompt"  # "prompt" = fill input box; "system" = inject as system override


@router.get("/api/quick-commands")
async def list_commands():
    items = _load()
    items.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
    return {"ok": True, "items": items}


@router.post("/api/quick-commands")
async def create_command(body: QuickCommandIn):
    items = _load()
    item = body.model_dump()
    item["id"] = "qc_" + uuid.uuid4().hex[:8]
    item["createdAt"] = datetime.now(timezone.utc).isoformat()
    items.append(item)
    _save(items)
    return {"ok": True, "item": item}


@router.put("/api/quick-commands/{cmd_id}")
async def update_command(cmd_id: str, body: QuickCommandIn):
    items = _load()
    for i, it in enumerate(items):
        if it.get("id") == cmd_id:
            items[i] = {**it, **body.model_dump(), "updatedAt": datetime.now(timezone.utc).isoformat()}
            _save(items)
            return {"ok": True, "item": items[i]}
    return {"ok": False, "error": "not found"}


@router.delete("/api/quick-commands/{cmd_id}")
async def delete_command(cmd_id: str):
    items = _load()
    items = [it for it in items if it.get("id") != cmd_id]
    _save(items)
    return {"ok": True}
