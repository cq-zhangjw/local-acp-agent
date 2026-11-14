"""Focused verification for single-file Agent configuration."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.agent_registry as registry  # noqa: E402
import utils.mcp_loader as mcp_loader  # noqa: E402
import api.manage.manager as manager  # noqa: E402


class AgentRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.agents = self.root / "agents"
        self.agents.mkdir()
        self.legacy = self.agents / "agents.json"
        self.patches = [
            patch.object(registry, "AGENTS_DIR", self.agents),
            patch.object(registry, "LEGACY_AGENTS_FILE", self.legacy),
        ]
        for item in self.patches:
            item.start()
        registry.invalidate_agents_cache()

    def tearDown(self) -> None:
        registry.invalidate_agents_cache()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_round_trip_uses_compact_yaml_references(self) -> None:
        registry.write_agent(
            "coder",
            {
                "description": "代码助手",
                "model": {"model_name": "test-model", "api_key": "must-not-persist"},
                "skills": ["review", "debug"],
                "tools": {
                    "inner_tools": ["read_file", "edit_file"],
                    "mcp_tools": [
                        {
                            "server": "calc_server",
                            "tools": [
                                {"name": "calculate", "inputSchema": {"type": "object"}},
                            ],
                        }
                    ],
                },
                "subagents": ["reviewer"],
                "knowledge": ["db_product_docs_1234"],
            },
            "# Coder\n\nDo the work.",
        )

        text = (self.agents / "coder.agent.md").read_text(encoding="utf-8")
        self.assertIn("- read_file", text)
        self.assertIn("- calc_server/calculate", text)
        self.assertNotIn("builtin:", text)
        self.assertNotIn("mcp:", text)
        self.assertNotIn("inputSchema", text)
        self.assertNotIn("api_key", text)

        loaded = registry.load_agents_config()["coder"]
        self.assertEqual(loaded["skills"], ["review", "debug"])
        self.assertEqual(loaded["model"], "test-model")
        self.assertEqual(loaded["knowledge"], ["db_product_docs_1234"])
        self.assertEqual(
            loaded["tools"]["mcp_tools"],
            [{"server": "calc_server", "tools": ["calculate"]}],
        )
        self.assertEqual(loaded["body"], "# Coder\n\nDo the work.\n")

    def test_filename_is_the_agent_identity(self) -> None:
        path = self.agents / "coder.agent.md"
        path.write_text("---\nname: other\n---\n\nbody\n", encoding="utf-8")
        with self.assertRaisesRegex(registry.AgentConfigError, "does not match filename"):
            registry.read_agent("coder")

    def test_invalid_yaml_is_reported(self) -> None:
        path = self.agents / "broken.agent.md"
        path.write_text("---\ntools: [\n---\n\nbody\n", encoding="utf-8")
        with self.assertRaisesRegex(registry.AgentConfigError, "invalid YAML"):
            registry.load_agents_config()

    def test_legacy_json_migration_preserves_body_and_compacts_tools(self) -> None:
        (self.agents / "default.agent.md").write_text(
            "---\ndescription: Existing description\n---\n\n# Existing body\n",
            encoding="utf-8",
        )
        self.legacy.write_text(
            json.dumps(
                {
                    "default": {
                        "skills": ["review"],
                        "tools": {
                            "inner_tools": ["read_file"],
                            "mcp_tools": [
                                {
                                    "server": "calc_server",
                                    "serverInfo": {"name": "Calculator"},
                                    "tools": [
                                        {
                                            "name": "calculate",
                                            "description": "large metadata",
                                            "inputSchema": {"type": "object"},
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        loaded = registry.load_agents_config()["default"]

        self.assertFalse(self.legacy.exists())
        self.assertTrue((self.agents / "agents.json.migrated.bak").exists())
        self.assertEqual(loaded["description"], "Existing description")
        self.assertEqual(loaded["body"], "# Existing body\n")
        self.assertEqual(
            loaded["tools"]["mcp_tools"],
            [{"server": "calc_server", "tools": ["calculate"]}],
        )


class McpReferenceResolutionTests(unittest.TestCase):
    def test_standalone_tool_load_does_not_require_agent_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server_dir = root / "browser_server"
            server_dir.mkdir()
            (server_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "search_web",
                                "description": "Search the web",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            holder = object()
            with (
                patch.object(mcp_loader, "MCP_SERVERS_DIR", root),
                patch.object(mcp_loader, "_holder", return_value=holder),
                patch.object(mcp_loader, "make_tool", return_value="search tool") as make_tool,
            ):
                result = mcp_loader.get_mcp_tool("browser_server", "search_web")

        self.assertEqual(result, "search tool")
        make_tool.assert_called_once_with(
            holder,
            {
                "name": "search_web",
                "description": "Search the web",
                "inputSchema": {"type": "object"},
            },
        )

    def test_selected_tool_name_resolves_metadata_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server_dir = root / "calc_server"
            server_dir.mkdir()
            (server_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "calculate",
                                "description": "Calculate",
                                "inputSchema": {"type": "object"},
                            },
                            {"name": "unused", "inputSchema": {"type": "object"}},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "coder": {
                    "tools": {
                        "mcp_tools": [
                            {"server": "calc_server", "tools": ["calculate"]}
                        ]
                    }
                }
            }
            holder = object()
            with (
                patch.object(mcp_loader, "MCP_SERVERS_DIR", root),
                patch.object(mcp_loader, "load_agents_config", return_value=config),
                patch.object(mcp_loader, "_holder", return_value=holder),
                patch.object(
                    mcp_loader,
                    "make_tool",
                    side_effect=lambda holder_arg, meta: (
                        meta["name"] if holder_arg is holder else "unexpected-holder"
                    ),
                ),
            ):
                mcp_loader.clear_tools_cache()
                self.assertEqual(mcp_loader.get_agent_mcp_tools("coder"), ["calculate"])


class AgentManagementNormalizationTests(unittest.TestCase):
    def test_preserves_granular_tools_and_adds_knowledge_retrieval(self) -> None:
        normalized = manager._normalize_agent_def(
            {
                "name": "coder",
                "tools": {
                    "inner_tools": [],
                    "mcp_tools": [
                        {"server": "calc_server", "tools": ["calculate"]},
                    ],
                },
                "knowledge": ["db_product_docs_1234"],
            }
        )

        self.assertEqual(normalized["tools"]["inner_tools"], [])
        self.assertIn(
            {"server": "calc_server", "tools": ["calculate"]},
            normalized["tools"]["mcp_tools"],
        )
        self.assertIn(
            {"server": "knowledge_server", "tools": ["retrieve_knowledge"]},
            normalized["tools"]["mcp_tools"],
        )
        self.assertEqual(normalized["knowledge"], ["db_product_docs_1234"])


if __name__ == "__main__":
    unittest.main()
