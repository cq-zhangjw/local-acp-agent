"""FastAPI bridge gateway: Web frontend <-> DeepAgents ACP Agent (stdio subprocess).

Responsibilities:
1. POST /upload          —— frontend uploads images/files, saves them, and returns an accessible HTTP resource URL
2. WS   /acp-ws          —— bidirectional forwarding between the frontend ACP client and the ACP agent subprocess
                           ACP JSON-RPC messages (including session/update, request_permission,
                           request_input, and other events)
3. /, /uploads           —— built Vue client and uploaded-file hosting

Usage: python gateway.py
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from api.chat_voice.chat_voice import router as chat_voice_router
from api.attachments import router as attachments_router
from api.preview_file import router as preview_router
from api.reveal_file import router as reveal_router
from api.context import router as context_router
from api.history import router as history_router
from api.todos import router as todos_router
from api.knowledge import router as knowledge_router
from api.manage.manager import router as manage_router
from api.timers import router as timers_router
from api.quick_commands import router as quick_commands_router

from dotenv import load_dotenv
load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
# Keep static/ for the legacy demo assets; the active SPA is built into web/dist.
WEB_DIST_DIR = BASE_DIR / "web" / "dist"
UPLOAD_DIR = Path(os.getenv("UPLOAD_ROOT", "uploads").strip() or "uploads")
if not UPLOAD_DIR.is_absolute():
    UPLOAD_DIR = BASE_DIR / UPLOAD_DIR
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ACP agent subprocess startup command
AGENT_CMD = [sys.executable, str(BASE_DIR / "acp_agent.py")]


# ---------- Local model service (API_MODE=local) ----------
# With API_MODE=local the gateway spawns llama-server (plugins/llama-cpp-*/)
# against the GGUF files under models/ and serves the same endpoint that
# model_config.json points at. The subprocess is terminated with the app.
_MODEL_LOGGER = logging.getLogger("gateway.local_model")
LOCAL_MODEL_PROC = None  # asyncio.subprocess.Process | None
LOCAL_MODEL_READY = False
_LOCAL_MODEL_LOG_HANDLE = None


def _api_mode() -> str:
    """Model API mode: 'api' (model_config.json endpoint, default) or 'local'."""
    return (os.getenv("API_MODE", "api") or "api").strip().lower()


def _local_model_config() -> dict:
    return {
        "server": BASE_DIR / (os.getenv("LOCAL_LLAMA_SERVER") or "plugins/llama-cpp-cu124/llama-server.exe"),
        "model": BASE_DIR / (os.getenv("LOCAL_MODEL_FILE") or "models/Ornith-1.5-9B-uncensored.Q6_K.gguf"),
        "mmproj": BASE_DIR / (os.getenv("LOCAL_MMPROJ_FILE") or "models/Ornith-1.5-9B-uncensored.mmproj-Q8_0.gguf"),
        "port": int(os.getenv("LOCAL_MODEL_PORT") or 8088),
        "ctx": int(os.getenv("LOCAL_MODEL_CTX") or 262144),
        "ngl": int(os.getenv("LOCAL_MODEL_NGL") or 99),
        "timeout": float(os.getenv("LOCAL_MODEL_START_TIMEOUT") or 300),
    }


def _port_open(port: int, timeout: float = 0.5) -> bool:
    """Whether something already accepts TCP connections on 127.0.0.1:port."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


_LOCAL_MODEL_JOB_HANDLE = None


def _bind_kill_on_parent_exit(pid: int) -> None:
    """Bind a child to a Job Object with KILL_ON_JOB_CLOSE (Windows).

    The launcher (app.exe) stops the gateway with Kill(entireProcessTree:true),
    which never reaches the lifespan finally block. Keeping this job handle
    open means Windows closes it when this process dies -- even on a hard
    kill -- and the assigned llama-server dies with it.
    """
    global _LOCAL_MODEL_JOB_HANDLE
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class _BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC_LIMIT),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        # 9 = JobObjectExtendedLimitInformation
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return
        # PROCESS_SET_QUOTA (0x0100) | PROCESS_TERMINATE (0x0001)
        hproc = kernel32.OpenProcess(0x0101, False, pid)
        if not hproc:
            kernel32.CloseHandle(job)
            return
        assigned = kernel32.AssignProcessToJobObject(job, hproc)
        kernel32.CloseHandle(hproc)
        if not assigned:
            kernel32.CloseHandle(job)
            return
        # Keep the handle open for the life of the gateway.
        _LOCAL_MODEL_JOB_HANDLE = job
        _MODEL_LOGGER.info("llama-server bound to job object (killed with the app)")
    except Exception:  # noqa: BLE001 - hardening only, never fatal
        _MODEL_LOGGER.debug("job object binding skipped", exc_info=True)


async def _wait_local_model_ready(port: int, timeout: float) -> None:
    """Background probe: log once llama-server accepts connections (or dies)."""
    import time

    global LOCAL_MODEL_READY
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = LOCAL_MODEL_PROC
        if proc is not None and proc.returncode is not None:
            _MODEL_LOGGER.error(
                "llama-server exited during startup (code %s); see logs/llama-server.log",
                proc.returncode,
            )
            return
        if await asyncio.to_thread(_port_open, port):
            LOCAL_MODEL_READY = True
            _MODEL_LOGGER.info("local model ready on http://127.0.0.1:%s", port)
            return
        await asyncio.sleep(2)
    _MODEL_LOGGER.warning(
        "local model not ready within %.0fs (it may still be loading)", timeout
    )


def _local_model_alias(port: int) -> str:
    """Model alias for llama-server --alias.

    Derived from model_config.json: the entry whose endpoint points at our
    port. Falls back to LOCAL_MODEL_ALIAS so the name the frontend sends
    matches what the server advertises.
    """
    try:
        entries = json.loads((BASE_DIR / "model_config.json").read_text(encoding="utf-8"))
        for item in entries:
            endpoint = str(item.get("endpoint") or "")
            if endpoint and endpoint.rstrip("/").endswith(f":{port}"):
                return str(item.get("model_name") or "")
    except Exception:  # noqa: BLE001 - alias is cosmetic, never fatal
        pass
    return (os.getenv("LOCAL_MODEL_ALIAS") or "").strip()


async def _start_local_model() -> None:
    """API_MODE=local: spawn llama-server (no-op for api mode or a live port)."""
    global LOCAL_MODEL_PROC, _LOCAL_MODEL_LOG_HANDLE

    if _api_mode() != "local":
        return
    cfg = _local_model_config()
    if not cfg["server"].exists() or not cfg["model"].exists():
        _MODEL_LOGGER.error(
            "local model files missing (server=%s model=%s); falling back to api mode",
            cfg["server"],
            cfg["model"],
        )
        return
    if await asyncio.to_thread(_port_open, cfg["port"]):
        _MODEL_LOGGER.info(
            "port %s already serving; reuse the running model service", cfg["port"]
        )
        return

    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _LOCAL_MODEL_LOG_HANDLE = (log_dir / "llama-server.log").open(
        "a", encoding="utf-8", errors="replace"
    )

    cmd = [str(cfg["server"]), "-m", str(cfg["model"])]
    if cfg["mmproj"].exists():
        cmd += ["--mmproj", str(cfg["mmproj"])]
    cmd += [
        "-c", str(cfg["ctx"]),
        "-ngl", str(cfg["ngl"]),
        "--host", "127.0.0.1",
        "--port", str(cfg["port"]),
    ]
    alias = _local_model_alias(cfg["port"])
    if alias:
        # Advertise the same model name model_config.json uses, so the
        # frontend-selected name resolves inside llama-server.
        cmd += ["--alias", alias]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        LOCAL_MODEL_PROC = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cfg["model"].parent),
            stdout=_LOCAL_MODEL_LOG_HANDLE.fileno(),
            stderr=_LOCAL_MODEL_LOG_HANDLE.fileno(),
            creationflags=creationflags,
        )
    except Exception:  # noqa: BLE001 - a missing server must not break startup
        _MODEL_LOGGER.exception("failed to start llama-server")
        return

    _MODEL_LOGGER.info(
        "llama-server started (pid %s, port %s, model %s)",
        LOCAL_MODEL_PROC.pid,
        cfg["port"],
        cfg["model"].name,
    )
    _bind_kill_on_parent_exit(LOCAL_MODEL_PROC.pid)
    asyncio.create_task(_wait_local_model_ready(cfg["port"], cfg["timeout"]))


async def _stop_local_model() -> None:
    """Terminate llama-server (and its process tree) when the app shuts down."""
    global LOCAL_MODEL_PROC, LOCAL_MODEL_READY, _LOCAL_MODEL_LOG_HANDLE

    proc = LOCAL_MODEL_PROC
    LOCAL_MODEL_PROC = None
    LOCAL_MODEL_READY = False
    if proc is not None and proc.returncode is None:
        _MODEL_LOGGER.info("stopping llama-server (pid %s)", proc.pid)
        try:
            await _terminate_process_tree(proc)
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass
    try:
        if _LOCAL_MODEL_LOG_HANDLE is not None:
            _LOCAL_MODEL_LOG_HANDLE.close()
    except Exception:  # noqa: BLE001
        pass
    _LOCAL_MODEL_LOG_HANDLE = None

def _should_open_browser() -> bool:
    """Whether the gateway should pop the default browser on startup.

    Off by default: the packaged app.exe hosts its own WebView window and
    launches the gateway itself. Set OPEN_BROWSER=1 to restore the browser
    popup for plain `python app.py` development runs.
    """
    return os.getenv("OPEN_BROWSER", "0").strip().lower() in ("1", "true", "yes", "on")


async def _open_home_in_default_browser(url: str) -> None:
    """Background task: open the gateway page in the default browser.

    Uses the stdlib webbrowser module (a plain system command) instead of
    Playwright: a startup launcher does not need a heavy automation engine.
    Never blocks or fails startup.
    """
    import webbrowser

    try:
        await asyncio.sleep(1)  # let the HTTP server finish binding
        await asyncio.to_thread(webbrowser.open, url)
    except Exception:  # noqa: BLE001 - browser launcher is a convenience
        pass


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    """Startup: optionally open the gateway page in the default browser, and
    start the scheduled-task ticker (TIMER_ENABLED, default on).

    The browser opens in a background task so startup is never blocked; the
    timer scheduler runs until the app shuts down.
    """
    if _should_open_browser():
        host = "127.0.0.1"
        port = int(os.getenv("APP_PORT") or 8000)
        asyncio.create_task(_open_home_in_default_browser(f"http://{host}:{port}"))

    # Local model service: API_MODE=local spawns llama-server on the endpoint
    # that model_config.json points at; it is killed with the app on shutdown.
    # 'api' (default) leaves the external endpoint untouched.
    await _start_local_model()

    # Recover tasks that were left in `running` by a previous process (D10),
    # then start the scheduler loop (skipped when TIMER_ENABLED=false).
    from api import timers_engine

    try:
        timers_engine.recover_running()
    except Exception:  # noqa: BLE001 - recovery must never break startup
        pass
    timers_task = asyncio.create_task(timers_engine.scheduler_loop())
    try:
        yield
    finally:
        # Graceful shutdown: cancel the scheduler with a hard timeout so
        # Ctrl+C never hangs on an in-flight timer run (agent init / model
        # call may block past cancellation).
        timers_task.cancel()
        try:
            await asyncio.wait_for(timers_task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:  # noqa: BLE001
            pass
        # Cancel any other lingering background tasks (browser launcher, etc.)
        current = asyncio.current_task()
        for task in asyncio.all_tasks():
            if task is current or task is timers_task:
                continue
            task.cancel()
        # Close MCP client subprocesses so the event loop can exit cleanly.
        try:
            from utils.mcp_loader import close_mcp_clients

            await close_mcp_clients()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass
        # Kill the local llama-server process tree (API_MODE=local) so it never
        # outlives the app.
        try:
            await _stop_local_model()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass


app = FastAPI(title="ACP client base", lifespan=_app_lifespan)

# voice call sub-router: /api/chat_voice/voices, /api/chat_voice/chat
app.include_router(chat_voice_router)
# history persistence sub-router: /api/history (MooFile BSON files)
app.include_router(history_router)
app.include_router(todos_router)
# knowledge base gateway sub-router: /api/knowledge (external moofile_base)
app.include_router(knowledge_router)
# management panel sub-router: /api/manage/skills, /api/manage/tools, /api/manage/agents
app.include_router(manage_router)
# context compaction sub-router: /api/context/compact
app.include_router(context_router)
# attachment (image/file) sub-router: /api/attachments (MooFile-backed)
app.include_router(attachments_router)
app.include_router(preview_router)
app.include_router(reveal_router)
# scheduled tasks sub-router: /api/timers (MooFile-backed, cron/interval)
app.include_router(timers_router)
app.include_router(quick_commands_router)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Save an upload and return its project-relative path for local Agent tools."""
    # Prevent path traversal: keep only the file name
    filename = Path(file.filename or "upload.bin").name
    save_path = UPLOAD_DIR / filename
    with save_path.open("wb") as f:
        f.write(await file.read())

    return {
        "path": f"/uploads/{filename}",
        "name": filename,
        "mimeType": file.content_type,
    }


@app.websocket("/acp-ws")
async def acp_bridge(websocket: WebSocket):
    """Each WebSocket connection corresponds to an independent ACP agent subprocess, forwarding messages bidirectionally."""
    await websocket.accept()
    # The frontend selects an agent by name; it is forwarded through the WS query
    # string and injected as AGENT_NAME so acp_agent.py assembles the right tools.
    agent_name = websocket.query_params.get("agent", "default") or "default"
    agent_env = os.environ.copy()
    agent_env["AGENT_NAME"] = agent_name
    # The frontend also picks the model by name; it travels the same way
    # (WS query param -> MODEL_NAME env) so model_util.create_model resolves
    # the matching entry in model_config.json per subprocess.
    model_name = websocket.query_params.get("model", "") or ""
    if model_name:
        agent_env["MODEL_NAME"] = model_name
    proc = await asyncio.create_subprocess_exec(
        *AGENT_CMD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(BASE_DIR),
        env=agent_env,
        # ACP JSON-RPC messages can include Base64 image blocks and exceed asyncio's 64 KiB default.
        limit=16 * 1024 * 1024,
    )

    async def forward_stdout():
        """Subprocess stdout (ACP events) -> frontend WebSocket"""
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8").strip()
            if text:
                try:
                    await websocket.send_text(text)
                except Exception:
                    break

    async def forward_stderr():
        """Keep agent diagnostics visible without mixing them into ACP stdout."""
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            print(f"[acp-agent] {line.decode('utf-8', errors='replace').rstrip()}")

    async def forward_ws():
        """Frontend WebSocket (ACP requests) -> subprocess stdin"""
        try:
            while True:
                msg = await websocket.receive_text()
                proc.stdin.write((msg + "\n").encode("utf-8"))
                await proc.stdin.drain()
        except WebSocketDisconnect:
            pass

    forwarder = asyncio.create_task(forward_stdout())
    error_forwarder = asyncio.create_task(forward_stderr())
    ws_reader = asyncio.create_task(forward_ws())
    # If either direction completes (usually the WS disconnects), terminate the subprocess to avoid agent leaks
    done, pending = await asyncio.wait(
        {ws_reader, forwarder, error_forwarder}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    # Diagnose cold-start failures: a nonzero/None returncode right after the
    # first message means the agent subprocess crashed (frontend then sees
    # "ACP connection closed"); stderr lines above carry the traceback.
    print(
        f"[acp-bridge] agent subprocess exited, returncode={proc.returncode}",
        flush=True,
    )
    await _terminate_process_tree(proc)


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate the subprocess and its entire process tree.

    On Windows, the venv python.exe is a launcher + real process pair.
    Only terminating the launcher may leave the busy real process behind as an orphan, so the whole tree must be killed.
    """
    if sys.platform == "win32":
        try:
            # taskkill /T recursively kills child processes, /F forces them
            await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            # Give taskkill a moment to finish
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            return
        except Exception:
            pass  # Fall back to regular terminate
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()


@app.get("/api/config")
async def get_config():
    """Serve the model list from model_config.json.

    The frontend shows every model entry and picks one; the chosen name is
    passed back via the WS query param (like agent), there is no active flag.
    content_size per model drives the frontend context-usage percentage.
    """
    from utils.model_util import list_models
    models = list_models()
    return {
        "models": [
            {"model_name": m.get("model_name"), "content_size": m.get("content_size") or 0}
            for m in models
            if m.get("model_name")
        ]
    }


@app.post("/api/jp-mark")
async def jp_mark(payload: dict):
    """Annotate Japanese kanji in a text with ruby readings (display layer only).

    Extracts spans that contain kana (平仮名/片仮名) and annotates only those
    spans with <ruby> tags via the jp_assistant MeCab engine. Non-Japanese
    parts are HTML-escaped as-is, so the returned HTML is safe to v-html.
    """
    from utils.jp_mark import jp_mark_text
    return jp_mark_text(str(payload.get("text") or ""))


# Mount the SPA after API and WebSocket routes so it only handles client asset requests.
app.mount("/", StaticFiles(directory=str(WEB_DIST_DIR), html=True), name="web-client")


@app.middleware("http")
async def no_cache_spa_index(request, call_next):
    """Prevent the SPA entry (index.html) from being cached so updates always take effect on refresh."""
    response = await call_next(request)
    if request.url.path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    import socket

    import uvicorn

    HOST = os.getenv("APP_HOST") if os.getenv("APP_HOST") else "127.0.0.1"
    PORT = int(os.getenv("APP_PORT")) if os.getenv("APP_PORT") else 8000

    # Friendly guard: if the port is already taken, the gateway is likely
    # running already. Exit with a clear message instead of starting a second
    # instance that races for the port (browser opens, then shuts down).
    # Probe via loopback: works regardless of APP_HOST being 0.0.0.0 or 127.0.0.1.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", PORT)) == 0:
            print(f"[gateway] 端口 {PORT} 已被占用，服务可能已在运行。")
            print("[gateway] 如需重启，请先停止旧实例（stop.ps1 或任务管理器结束对应 python 进程）。")
            sys.exit(1)

    uvicorn.run(
        "app:app",
        host=HOST,
        port=PORT,
        # Hard cap on graceful shutdown so Ctrl+C always exits even when a
        # websocket or background task does not finish promptly.
        timeout_graceful_shutdown=10,
    )