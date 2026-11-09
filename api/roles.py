"""Role presets CRUD — stored as individual .md files under .deepagents/roles/.

Each role is one markdown file whose filename (minus .md) is the role name.
This makes roles easy to share/edit alongside the repo, just like quick_commands.json.
"""
import logging
import re
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("api.roles")
router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DIR = _PROJECT_ROOT / ".deepagents" / "roles"


def _slug(name: str) -> str:
    # Allow Chinese filenames; just strip path separators.
    return re.sub(r'[\\/:*?"<>|]', "_", name.strip())


@router.get("/api/roles")
async def list_roles():
    if not _DIR.exists():
        return {"ok": True, "items": []}
    items = []
    for p in sorted(_DIR.glob("*.md")):
        try:
            content = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.exception("roles: failed to read %s", p)
            continue
        name = p.stem
        items.append({
            "name": name,
            "content": content,
            "preview": content.strip()[:120],
        })
    return {"ok": True, "items": items}


class RoleIn(BaseModel):
    content: str


@router.put("/api/roles/{name}")
async def upsert_role(name: str, body: RoleIn):
    _DIR.mkdir(parents=True, exist_ok=True)
    safe = _slug(name)
    path = _DIR / f"{safe}.md"
    path.write_text(body.content, encoding="utf-8")
    return {"ok": True, "item": {"name": safe, "content": body.content}}


@router.delete("/api/roles/{name}")
async def delete_role(name: str):
    safe = _slug(name)
    path = _DIR / f"{safe}.md"
    if path.exists():
        path.unlink()
    return {"ok": True}
