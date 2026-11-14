"""Focused verification for Agent-frontmatter-driven skill loading."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.skill_loader as skill_loader  # noqa: E402


class AgentSkillSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.skills_dir = Path(self.temp.name) / "skills"
        self.skills_dir.mkdir()
        self.skills_dir_patch = patch.object(skill_loader, "SKILLS_DIR", self.skills_dir)
        self.skills_dir_patch.start()

    def tearDown(self) -> None:
        self.skills_dir_patch.stop()
        self.temp.cleanup()

    def add_skill(self, name: str, *, disabled: bool = False) -> None:
        skill_dir = self.skills_dir / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: " + name + "\n---\n", encoding="utf-8")
        if disabled:
            (skill_dir / ".disabled").touch()

    def test_frontmatter_list_is_filtered_deduplicated_and_sorted(self) -> None:
        self.add_skill("zeta")
        self.add_skill("beta", disabled=True)
        self.add_skill("alpha")
        with patch.object(
            skill_loader,
            "load_agents_config",
            return_value={"coder": {"skills": ["zeta", "beta", "alpha", "missing", "zeta"]}},
        ):
            self.assertEqual(skill_loader.get_agent_skill_names("coder"), ["alpha", "zeta"])

    def test_empty_list_disables_all_and_missing_list_defaults_to_installed(self) -> None:
        self.add_skill("beta")
        self.add_skill("alpha")
        with patch.object(skill_loader, "load_agents_config", return_value={"coder": {"skills": []}}):
            self.assertEqual(skill_loader.get_agent_skill_names("coder"), [])
        with patch.object(skill_loader, "load_agents_config", return_value={"coder": {}}):
            self.assertEqual(skill_loader.get_agent_skill_names("coder"), ["alpha", "beta"])

    def test_agent_lookup_is_case_insensitive(self) -> None:
        self.add_skill("alpha")
        with patch.object(skill_loader, "load_agents_config", return_value={"chat": {"skills": []}}):
            self.assertEqual(skill_loader.get_agent_skill_names("Chat"), [])

    def test_selection_does_not_materialize_enabled_directory(self) -> None:
        self.add_skill("alpha")
        with patch.object(skill_loader, "load_agents_config", return_value={"coder": {"skills": ["alpha"]}}):
            self.assertEqual(skill_loader.get_agent_skill_names("coder"), ["alpha"])
        self.assertFalse((self.skills_dir / ".enabled").exists())

    def test_selected_skill_metadata_includes_path_and_description(self) -> None:
        self.add_skill("alpha")
        (self.skills_dir / "alpha" / "SKILL.md").write_text(
            "---\nname: alpha\ndescription: Alpha skill description\n---\nInstructions.\n",
            encoding="utf-8",
        )
        with patch.object(skill_loader, "load_agents_config", return_value={"coder": {"skills": ["alpha"]}}):
            metadata = skill_loader.get_agent_skill_metadata("coder")

        self.assertEqual(
            metadata,
            [
                {
                    "name": "alpha",
                    "skill_path": ".deepagents/skills/alpha/SKILL.md",
                    "description": "Alpha skill description",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
