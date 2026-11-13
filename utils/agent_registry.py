"""Single-file Agent registry backed by `.deepagents/agents/*.agent.md`.

The YAML frontmatter stores resource references; the Markdown body stores the
Agent system prompt. MCP metadata is deliberately not duplicated here.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
AGENTS_DIR = BASE_DIR / ".deepagents" / "agents"
LEGACY_AGENTS_FILE = AGENTS_DIR / "agents.json"
AGENT_SUFFIX = ".agent.md"
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MANAGED_FIELDS = {
    "schema",
    "name",
    "description",
    "longDescription",
    "enabled",
    "model",
    "system_prompt",
    "skills",
    "tools",
    "subagents",
    "knowledge",
}

_cache_signature: tuple[tuple[str, int, int], ...] | None = None
_cache: dict[str, dict[str, Any]] | None = None


class AgentConfigError(ValueError):
    """Raised when an Agent document cannot be parsed or validated."""


def validate_agent_name(name: str) -> str:
    name = (name or "").strip()
    if not _AGENT_NAME_RE.fullmatch(name):
        raise AgentConfigError(
            f"invalid agent name: {name!r} (use [A-Za-z0-9_-], max 64)"
        )
    return name


def agent_path(name: str) -> Path:
    return AGENTS_DIR / f"{validate_agent_name(name)}{AGENT_SUFFIX}"


def _split_document(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        raise AgentConfigError(f"{path}: YAML frontmatter is not closed")
    raw = text[4:end]
    try:
        metadata = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError as exc:
        raise AgentConfigError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise AgentConfigError(f"{path}: YAML frontmatter must be an object")
    body = text[end + 4 :].lstrip("\r\n")
    return metadata, body


def _tool_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        name = item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def _normalize_tools(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        builtin: list[str] = []
        mcp: dict[str, list[str]] = {}
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise AgentConfigError(
                    "tools entries must be strings: builtin name or server/tool"
                )
            ref = item.strip()
            if "/" not in ref:
                if ref not in builtin:
                    builtin.append(ref)
                continue
            server, separator, tool = ref.partition("/")
            if not separator or not server or not tool or "/" in tool:
                raise AgentConfigError(
                    f"invalid MCP tool reference {ref!r}; expected server/tool"
                )
            selected = mcp.setdefault(server, [])
            if tool not in selected:
                selected.append(tool)
        return {
            "inner_tools": builtin,
            "mcp_tools": [
                {"server": server, "tools": tools}
                for server, tools in mcp.items()
            ],
        }
    if not isinstance(value, dict):
        return None
    builtin = value.get("builtin")
    if builtin is None:
        builtin = value.get("inner_tools")
    result: dict[str, Any] = {}
    if isinstance(builtin, list):
        result["inner_tools"] = _tool_names(builtin)

    entries: list[dict[str, Any]] = []
    canonical_mcp = value.get("mcp")
    if isinstance(canonical_mcp, dict):
        for server, tools in canonical_mcp.items():
            if isinstance(server, str):
                selected = _tool_names(tools)
                if selected:
                    entries.append({"server": server, "tools": selected})
    else:
        for entry in value.get("mcp_tools") or []:
            if not isinstance(entry, dict):
                continue
            server = entry.get("server")
            if not server:
                server = (entry.get("serverInfo") or {}).get("name")
            selected = _tool_names(entry.get("tools"))
            if isinstance(server, str) and server and selected:
                entries.append({"server": server, "tools": selected})
    if entries:
        result["mcp_tools"] = entries
    elif "mcp" in value or "mcp_tools" in value:
        result["mcp_tools"] = []
    return result or None


def _normalize_metadata(name: str, metadata: dict[str, Any], body: str) -> dict[str, Any]:
    declared_name = metadata.get("name")
    if declared_name and declared_name != name:
        raise AgentConfigError(
            f"{agent_path(name)}: frontmatter name {declared_name!r} "
            f"does not match filename {name!r}"
        )
    data = {k: v for k, v in metadata.items() if k != "name"}
    data["schema"] = int(data.get("schema") or 1)
    data["description"] = str(data.get("description") or "")
    data["enabled"] = bool(data.get("enabled", True))
    model = data.get("model")
    if isinstance(model, dict):
        model = model.get("model_name")
    if isinstance(model, str) and model.strip():
        data["model"] = model.strip()
    else:
        data.pop("model", None)
    data.pop("system_prompt", None)
    for key in ("skills", "subagents", "knowledge"):
        if key in data:
            data[key] = _tool_names(data[key])
    tools = _normalize_tools(data.get("tools"))
    if tools is not None:
        data["tools"] = tools
    else:
        data.pop("tools", None)
    data["body"] = body
    return data


def read_agent(name: str) -> dict[str, Any]:
    name = validate_agent_name(name)
    path = agent_path(name)
    if not path.is_file():
        raise FileNotFoundError(path)
    metadata, body = _split_document(path.read_text(encoding="utf-8"), path)
    return _normalize_metadata(name, metadata, body)


def _signature() -> tuple[tuple[str, int, int], ...]:
    if not AGENTS_DIR.is_dir():
        return ()
    rows = []
    for path in sorted(AGENTS_DIR.glob(f"*{AGENT_SUFFIX}")):
        stat = path.stat()
        rows.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(rows)


def invalidate_agents_cache() -> None:
    global _cache, _cache_signature
    _cache = None
    _cache_signature = None


def load_agents_config() -> dict[str, dict[str, Any]]:
    global _cache, _cache_signature
    migrate_legacy_agents()
    signature = _signature()
    if _cache is not None and signature == _cache_signature:
        return _cache
    agents: dict[str, dict[str, Any]] = {}
    for filename, _, _ in signature:
        name = filename[: -len(AGENT_SUFFIX)]
        agents[name] = read_agent(name)
    _cache = agents
    _cache_signature = signature
    return agents


def _canonical_metadata(config: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        key: value
        for key, value in existing.items()
        if key not in _MANAGED_FIELDS and key != "body"
    }
    metadata["schema"] = int(config.get("schema") or existing.get("schema") or 1)
    description = config.get("description") or existing.get("description")
    if description:
        metadata["description"] = str(description)
    long_description = config.get("longDescription") or existing.get("longDescription")
    if long_description:
        metadata["longDescription"] = str(long_description)
    metadata["enabled"] = bool(config.get("enabled", True))
    model = config.get("model")
    if isinstance(model, dict):
        model = model.get("model_name")
    if isinstance(model, str) and model.strip():
        metadata["model"] = model.strip()
    if isinstance(config.get("skills"), list):
        metadata["skills"] = _tool_names(config["skills"])

    tools = _normalize_tools(config.get("tools"))
    if tools is not None:
        references = list(tools.get("inner_tools") or [])
        for entry in tools.get("mcp_tools") or []:
            server = entry.get("server")
            if not server:
                continue
            references.extend(
                f"{server}/{tool}"
                for tool in _tool_names(entry.get("tools"))
            )
        metadata["tools"] = references
    if isinstance(config.get("subagents"), list):
        metadata["subagents"] = _tool_names(config["subagents"])
    if isinstance(config.get("knowledge"), list):
        metadata["knowledge"] = _tool_names(config["knowledge"])
    return metadata


def write_agent(name: str, config: dict[str, Any], body: str | None = None) -> None:
    name = validate_agent_name(name)
    path = agent_path(name)
    existing: dict[str, Any] = {}
    existing_body = ""
    if path.is_file():
        existing, existing_body = _split_document(path.read_text(encoding="utf-8"), path)
    metadata = _canonical_metadata(config, existing)
    resolved_body = existing_body if body is None else body.strip()
    if not resolved_body:
        resolved_body = f"# {name}\n\nDefine the agent behavior here (system prompt / instructions)."
    yaml_text = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    ).rstrip()
    content = f"---\n{yaml_text}\n---\n\n{resolved_body.rstrip()}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    invalidate_agents_cache()


def write_agents_config(agents: dict[str, dict[str, Any]]) -> None:
    for name, config in agents.items():
        write_agent(name, config, config.get("body"))


def delete_agent(name: str) -> None:
    path = agent_path(name)
    path.unlink(missing_ok=True)
    invalidate_agents_cache()


def migrate_legacy_agents() -> None:
    if not LEGACY_AGENTS_FILE.is_file():
        return
    try:
        legacy = json.loads(LEGACY_AGENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentConfigError(f"failed to read legacy {LEGACY_AGENTS_FILE}: {exc}") from exc
    if not isinstance(legacy, dict):
        raise AgentConfigError(f"{LEGACY_AGENTS_FILE}: root must be an object")
    for name, config in legacy.items():
        if not isinstance(config, dict):
            raise AgentConfigError(f"{LEGACY_AGENTS_FILE}: agent {name!r} must be an object")
        body = read_agent(name).get("body", "") if agent_path(name).is_file() else ""
        write_agent(name, config, body)
    backup = LEGACY_AGENTS_FILE.with_name("agents.json.migrated.bak")
    LEGACY_AGENTS_FILE.replace(backup)
    logger.info("migrated legacy Agent config to .agent.md files; backup: %s", backup)
    invalidate_agents_cache()

