"""Load only the skills associated with an Agent document."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from utils.mcp_loader import load_agents_config

BASE_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = BASE_DIR / ".deepagents" / "skills"
SKILLS_SOURCE = ".deepagents/skills"


def list_skill_names() -> list[str]:
    """Directory names of installed skills (stable identity, sorted)."""
    names = []
    if SKILLS_DIR.is_dir():
        for d in sorted(SKILLS_DIR.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                names.append(d.name)
    return names


def _selected_names(agent_name: str) -> list[str]:
    installed = set(list_skill_names())
    # hard-disable: a .disabled marker removes the skill from every agent
    available = {
        name for name in installed
        if not (SKILLS_DIR / name / ".disabled").exists()
    }
    name = (agent_name or os.getenv("AGENT_NAME", "default")).strip() or "default"
    configs = load_agents_config()
    cfg = configs.get(name)
    if cfg is None:
        cfg = next(
            (config for key, config in configs.items() if key.casefold() == name.casefold()),
            {},
        )
    skills = cfg.get("skills")
    if not isinstance(skills, list):
        return sorted(available)
    wanted = {skill for skill in skills if isinstance(skill, str)}
    return [skill for skill in sorted(available) if skill in wanted]


def get_agent_skill_names(agent_name: str | None = None) -> list[str]:
    """Read the selected skill names from the Agent document frontmatter."""
    name = (agent_name or os.getenv("AGENT_NAME", "default")).strip() or "default"
    return _selected_names(name)


def get_agent_skill_metadata(agent_name: str | None = None) -> list[dict[str, str]]:
    """Return selected skill names, paths, and descriptions for prompt injection."""
    entries = []
    for name in get_agent_skill_names(agent_name):
        skill_path = SKILLS_DIR / name / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        metadata: dict = {}
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end < 0:
                raise ValueError(f"{skill_path}: unclosed YAML frontmatter")
            try:
                parsed = yaml.safe_load(content[3:end])
            except yaml.YAMLError as exc:
                raise ValueError(f"{skill_path}: invalid YAML frontmatter") from exc
            if parsed is not None and not isinstance(parsed, dict):
                raise ValueError(f"{skill_path}: frontmatter must be a mapping")
            metadata = parsed or {}

        description = metadata.get("description", "")
        if not isinstance(description, str):
            raise ValueError(f"{skill_path}: description must be a string")
        entries.append(
            {
                "name": name,
                "skill_path": f"{SKILLS_SOURCE}/{name}/SKILL.md",
                "description": " ".join(description.split()),
            }
        )
    return entries
