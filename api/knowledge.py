"""Knowledge base (MooFile Base) gateway - sub-router, prefix /api/knowledge.

Bridges the frontend knowledge panel to the external moofile_base project:
- GET  /api/knowledge/config                       -> {baseUrl, topK, threshold, reachable}
- GET  /api/knowledge/databases?agent=<name>       -> list of knowledge bases (+ enabled)
- POST /api/knowledge/start                        -> launch the external moofile_base backend
- POST /api/knowledge/stop                         -> terminate the external moofile_base backend
- POST /api/knowledge/databases/{db_id}/toggle?agent=<name> -> associate or
       disassociate one knowledge base with the agent

The AI queries associated knowledge through the knowledge_server MCP tools.
Knowledge-base IDs and MCP tool selections are stored independently; selecting
a knowledge base guarantees that retrieve_knowledge is available.
"""
import json
import logging
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException

from utils.agent_registry import load_agents_config, write_agents_config
from utils.mcp_loader import clear_tools_cache, invalidate_agents_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

BASE_DIR = Path(__file__).resolve().parent.parent  # project root (api/ -> root)
KNOWLEDGE_SERVER = "knowledge_server"
_DEFAULT_TIMEOUT = 2.0
_CREATE_NO_WINDOW = 0x08000000  # Windows: do not pop a console for the child


def _base_url() -> str:
    """Base URL of the external MooFile backend (env-overridable)."""
    return os.environ.get("MOOFILE_BASE_URL", "http://127.0.0.1:8888").rstrip("/")


def _mb_root() -> Path:
    """Root directory of the external moofile_base project.

    Priority: MOOFILE_BASE_ROOT_DIR env var (absolute, or relative to the ACP
    project root) -> sibling directory next to this project.
    """
    raw = os.environ.get("MOOFILE_BASE_ROOT_DIR", "").strip()
    if raw:
        root = Path(raw)
        if not root.is_absolute():
            root = BASE_DIR / root
        return root
    return BASE_DIR.parent / "moofile_base"


def _probe(base: str | None = None) -> bool:
    """Return True when the MooFile backend health endpoint answers code 0."""
    url = (base or _base_url()).rstrip("/") + "/api/system/health"
    try:
        with urllib.request.urlopen(url, timeout=_DEFAULT_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status == 200 and body.get("code") == 0
    except Exception:  # noqa: BLE001 - probe failures just mean offline
        return False


def _get_json(path: str, base: str | None = None) -> object:
    """GET a JSON payload from the MooFile backend or raise 502."""
    url = (base or _base_url()).rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=_DEFAULT_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"knowledge backend unreachable: {exc}") from exc


def _read_agents() -> dict:
    return load_agents_config()


def _write_agents(data: dict) -> None:
    write_agents_config(data)
    invalidate_agents_cache()


def _agent_knowledge_ids(agent: str) -> set[str]:
    """Return the exact knowledge-base IDs associated with an agent."""
    cfg = _read_agents().get(agent) or {}
    return {
        item
        for item in cfg.get("knowledge") or []
        if isinstance(item, str) and item
    }


@router.get("/config")
def knowledge_config():
    """Gateway config + liveness of the external knowledge backend."""
    base = _base_url()
    top_k = int(os.environ.get("MOOFILE_RETRIEVAL_TOP_K", "5") or 5)
    raw_threshold = os.environ.get("MOOFILE_RETRIEVAL_THRESHOLD", "").strip()
    threshold = float(raw_threshold) if raw_threshold else None
    return {
        "baseUrl": base,
        "topK": top_k,
        "threshold": threshold,
        "reachable": _probe(base),
    }


@router.get("/databases")
def knowledge_databases(agent: str = "default"):
    """List vector knowledge bases from moofile_base, tagged per-agent enabled.

    Only vector databases are shown - plain collections (like the history
    store under the same db/ root) are management/backend data, not user
    knowledge bases. When the backend is offline the response is an empty
    list: the frontend surfaces the offline hint (config.reachable) instead
    of a hard error, so the panel never hangs or spams failures.
    """
    try:
        envelope = _get_json("/api/databases")
    except HTTPException:
        return []
    rows = envelope.get("data") if isinstance(envelope, dict) else envelope
    enabled_ids = _agent_knowledge_ids(agent or "default")
    out = []
    for db in rows or []:
        if db.get("type") != "vector":
            continue
        out.append(
            {
                "id": db.get("id"),
                "name": db.get("name"),
                "type": db.get("type", "normal"),
                "recordCount": db.get("recordCount", 0),
                "documentCount": db.get("documentCount", 0),
                "enabled": db.get("id") in enabled_ids,
            }
        )
    return out


@router.post("/start")
def start_knowledge_service():
    """Launch the external moofile_base backend when it is not running.

    Spawns moofile_base's own venv (app.py, :8888) with the shared db root and
    no auto MCP, waits up to 20s for its health endpoint, then clears the MCP
    tool cache so the next agent assembly re-injects the knowledge tools.
    """
    if _probe():
        return {"ok": True, "already": True, "started": False}

    mb_root = _mb_root()
    py = mb_root / ".venv" / "Scripts" / "python.exe"
    if not (mb_root / "app.py").is_file():
        raise HTTPException(status_code=400, detail=f"moofile_base not found at {mb_root}")
    if not py.is_file():
        raise HTTPException(
            status_code=400,
            detail="moofile_base venv not found (run its own dependency install first)",
        )

    logs = mb_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "MOOFILE_DB_DIR": str(BASE_DIR / "db"),
        "MOOFILE_WITH_MCP": "false",
    }
    try:
        with open(logs / "app.out.log", "a", encoding="utf-8") as out, open(
            logs / "app.err.log", "a", encoding="utf-8"
        ) as err:
            subprocess.Popen(
                [str(py), "app.py"],
                cwd=str(mb_root),
                env=env,
                stdout=out,
                stderr=err,
                creationflags=_CREATE_NO_WINDOW,
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to launch moofile_base: {exc}") from exc

    for _ in range(20):
        time.sleep(1)
        if _probe():
            clear_tools_cache()
            invalidate_agents_cache()
            return {"ok": True, "already": False, "started": True}

    raise HTTPException(
        status_code=504,
        detail="moofile_base did not become ready within 20s (check moofile_base/logs/app.err.log)",
    )


# Windows: terminate processes anchored to the moofile_base project (command
# line / executable path match), then fall back to whatever listens on :8888.
# Mirrors moofile_base/stop.ps1 so the panel can stop the backend on demand.
_STOP_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$targets = Get-CimInstance Win32_Process | Where-Object {
    ($_.CommandLine -match 'app\.py') -and
    ($_.ExecutablePath -match 'moofile_base' -or $_.CommandLine -match 'moofile_base')
}
foreach ($t in $targets) {
    $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $t.ProcessId }
    foreach ($c in $children) { Stop-Process -Id $c.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Process -Id $t.ProcessId -Force -ErrorAction SilentlyContinue
}
$listener = Get-NetTCPConnection -LocalPort 8888 -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listener) { Stop-Process -Id $l.OwningProcess -Force -ErrorAction SilentlyContinue }
"""


@router.post("/stop")
def stop_knowledge_service():
    """Stop the external moofile_base backend when it is running.

    Kills the moofile_base app.py process tree (anchored by executable path /
    command line) with a :8888 listener fallback, then clears the MCP tool
    cache so the next agent assembly drops the knowledge tools.
    """
    if not _probe():
        return {"ok": True, "wasRunning": False}

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _STOP_SCRIPT],
            cwd=str(BASE_DIR),
            timeout=30,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to stop moofile_base: {exc}") from exc

    for _ in range(10):
        time.sleep(1)
        if not _probe():
            clear_tools_cache()
            invalidate_agents_cache()
            return {"ok": True, "wasRunning": True}

    raise HTTPException(
        status_code=500,
        detail="moofile_base did not stop within 10s (check running processes on :8888)",
    )


@router.post("/databases/{db_id}/toggle")
def toggle_knowledge(db_id: str, agent: str = "default"):
    """Associate one knowledge base and ensure its retrieval tool is available."""
    db_id = db_id.strip()
    if not db_id or len(db_id) > 256:
        raise HTTPException(status_code=400, detail="invalid knowledge database id")
    agent = agent or "default"
    if not _probe():
        raise HTTPException(status_code=503, detail="knowledge backend unreachable")
    agents = _read_agents()
    cfg = agents.setdefault(agent, {})
    knowledge = [
        item for item in cfg.get("knowledge") or []
        if isinstance(item, str) and item
    ]
    if db_id in knowledge:
        cfg["knowledge"] = [item for item in knowledge if item != db_id]
        enabled = False
    else:
        cfg["knowledge"] = knowledge + [db_id]
        enabled = True

    tools = cfg.setdefault("tools", {})
    mcp_tools = tools.setdefault("mcp_tools", [])
    entry = next((e for e in mcp_tools if e.get("server") == KNOWLEDGE_SERVER), None)
    if enabled and entry is None:
        manifest_path = (
            BASE_DIR / ".deepagents" / "tools" / "mcp_servers" / KNOWLEDGE_SERVER / "manifest.json"
        )
        if manifest_path.is_file():
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        else:
            manifest = {}
        available = {
            tool.get("name")
            for tool in manifest.get("tools") or []
            if isinstance(tool, dict) and tool.get("name")
        }
        selected = ["retrieve_knowledge"] if "retrieve_knowledge" in available else []
        mcp_tools.append({"server": KNOWLEDGE_SERVER, "tools": selected})
    elif enabled and entry is not None:
        selected = [
            item if isinstance(item, str) else item.get("name")
            for item in entry.get("tools") or []
            if isinstance(item, str) or isinstance(item, dict)
        ]
        if "retrieve_knowledge" not in selected:
            entry["tools"] = [item for item in selected if item] + ["retrieve_knowledge"]
    _write_agents(agents)
    return {"enabled": enabled}
