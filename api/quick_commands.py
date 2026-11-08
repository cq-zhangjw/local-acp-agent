"""Quick commands CRUD — stored in .deepagents/quick_commands.json."""
import json
import logging
import time
import uuid
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("api.quick_commands")
router = APIRouter()

STORE = Path(".deepagents/quick_commands.json")


def _load() -> list[dict]:
    if not STORE.exists():
        return []
    try:
        with open(STORE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save(items: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


class QuickCommandIn(BaseModel):
    title: str
    content: str
    agent: str = "default"
    model: str = ""
    mode: str = "task"
    autoSend: bool = False


@router.get("/api/quick-commands")
async def list_commands():
    items = _load()
    items.sort(key=lambda x: x.get("createdAt", 0), reverse=True)
    return {"ok": True, "items": items}


@router.post("/api/quick-commands")
async def create_command(body: QuickCommandIn):
    items = _load()
    item = body.model_dump()
    item["id"] = "qc_" + uuid.uuid4().hex[:8]
    item["createdAt"] = int(time.time() * 1000)
    items.append(item)
    _save(items)
    return {"ok": True, "item": item}


@router.put("/api/quick-commands/{cmd_id}")
async def update_command(cmd_id: str, body: QuickCommandIn):
    items = _load()
    for i, it in enumerate(items):
        if it.get("id") == cmd_id:
            updated = {**it, **body.model_dump()}
            items[i] = updated
            _save(items)
            return {"ok": True, "item": updated}
    return {"ok": False, "error": "not found"}


@router.delete("/api/quick-commands/{cmd_id}")
async def delete_command(cmd_id: str):
    items = _load()
    items = [it for it in items if it.get("id") != cmd_id]
    _save(items)
    return {"ok": True}
