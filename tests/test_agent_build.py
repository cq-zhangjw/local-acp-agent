"""Focused tests for Agent-frontmatter-driven agent construction."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import acp_agent  # noqa: E402


class AgentToolPolicyTests(unittest.TestCase):
    def test_system_prompt_falls_back_when_agent_has_no_knowledge_bases(self) -> None:
        with (
            patch.object(acp_agent, "_load_agent_md", return_value="Chat description"),
            patch.object(acp_agent, "_knowledge_scope_prompt", return_value=""),
        ):
            prompt = acp_agent.get_system_prompt("chat")

        self.assertIn("No associated knowledge bases.", prompt)

    def test_system_prompt_injects_enabled_skill_paths_and_descriptions(self) -> None:
        with (
            patch.object(acp_agent, "get_system_prompt", return_value="base"),
            patch.object(
                acp_agent,
                "get_agent_skill_metadata",
                return_value=[
                    {
                        "name": "agent-creator",
                        "skill_path": ".deepagents/skills/agent-creator/SKILL.md",
                        "description": "Create Agents",
                    },
                    {
                        "name": "excel-skill",
                        "skill_path": ".deepagents/skills/excel-skill/SKILL.md",
                        "description": "Work with spreadsheets",
                    },
                ],
            ),
        ):
            prompt = acp_agent.build_system_prompt("default")

        self.assertIn("- agent-creator", prompt)
        self.assertIn("- excel-skill", prompt)
        self.assertIn("skill_path: .deepagents/skills/agent-creator/SKILL.md", prompt)
        self.assertIn("description: Create Agents", prompt)
        self.assertIn("description: Work with spreadsheets", prompt)

    def test_search_prompts_are_english_and_match_toggle(self) -> None:
        with (
            patch.object(acp_agent, "get_system_prompt", return_value="base"),
            patch.object(acp_agent, "get_agent_skill_metadata", return_value=[]),
        ):
            enabled = acp_agent.build_system_prompt(web_search=True)
            disabled = acp_agent.build_system_prompt(web_search=False)

        self.assertIn("Web search is enabled.", enabled)
        self.assertIn("mcp_browser_server_search_web", enabled)
        self.assertIn("Web search is disabled.", disabled)
        self.assertIn("Do not call", disabled)
        self.assertTrue(all(ord(character) < 128 for character in enabled + disabled))

    def test_search_toggle_controls_search_tool_injection(self) -> None:
        search_tool = SimpleNamespace(name="mcp_browser_server_search_web")
        other_tool = SimpleNamespace(name="mcp_browser_server_fetch_webpage")

        with patch.object(acp_agent, "get_mcp_tool", return_value=search_tool) as load_tool:
            disabled_tools = acp_agent._configure_web_search_tool(False, [search_tool, other_tool])
            self.assertEqual(disabled_tools, [other_tool])
            load_tool.assert_not_called()

            enabled_tools = acp_agent._configure_web_search_tool(True, [other_tool])
            self.assertEqual(enabled_tools, [other_tool, search_tool])
            load_tool.assert_called_once_with("browser_server", "search_web")

            already_enabled = acp_agent._configure_web_search_tool(
                True,
                [search_tool, other_tool, search_tool],
            )
            self.assertEqual(already_enabled, [other_tool, search_tool])
            load_tool.assert_called_once()

    def test_missing_tool_config_preserves_default_tool_set(self) -> None:
        filesystem_tools, todo_tools, excluded = acp_agent._agent_tool_policy({}, "default")

        self.assertEqual(filesystem_tools, "all")
        self.assertEqual(
            {tool.name for tool in todo_tools},
            {"create_todo", "update_todo", "get_todo"},
        )
        self.assertEqual(excluded, frozenset())

    def test_explicit_tool_config_is_an_allowlist(self) -> None:
        filesystem_tools, todo_tools, excluded = acp_agent._agent_tool_policy(
            {"tools": {"inner_tools": ["read_file", "task"]}},
            "coder",
        )

        self.assertEqual(filesystem_tools, ["read_file"])
        self.assertEqual(todo_tools, [])
        self.assertNotIn("read_file", excluded)
        self.assertNotIn("task", excluded)
        self.assertIn("write_file", excluded)
        self.assertIn("create_todo", excluded)

    def test_empty_tool_config_disables_builtin_and_todo_tools(self) -> None:
        filesystem_tools, todo_tools, excluded = acp_agent._agent_tool_policy(
            {"tools": {"inner_tools": []}},
            "chat",
        )

        self.assertEqual(filesystem_tools, ["read_file"])
        self.assertEqual(todo_tools, [])
        self.assertIn("read_file", excluded)
        self.assertIn("task", excluded)
        self.assertIn("create_todo", excluded)

    def test_unknown_builtin_tool_is_reported(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported built-in tools: typo"):
            acp_agent._agent_tool_policy(
                {"tools": {"inner_tools": ["typo"]}},
                "coder",
            )


class BuildAgentTests(unittest.TestCase):
    def test_chat_uses_yaml_tools_skills_and_subagent_config(self) -> None:
        agents = {
            "chat": {
                "skills": [],
                "tools": {"inner_tools": ["read_file", "task"], "mcp_tools": []},
            },
            "worker": {
                "skills": ["agent-creator"],
                "tools": {"inner_tools": ["write_file", "todo"], "mcp_tools": []},
            },
        }
        filesystem_instances = []

        def filesystem_middleware(**kwargs):
            middleware = SimpleNamespace(name="FilesystemMiddleware", options=kwargs)
            filesystem_instances.append(middleware)
            return middleware

        with (
            patch.dict("os.environ", {"WEB_SEARCH": "0"}),
            patch.object(acp_agent, "load_agents_config", return_value=agents),
            patch.object(acp_agent, "get_agent_mcp_tools", return_value=[]) as get_mcp_tools,
            patch.object(
                acp_agent,
                "get_agent_subagents_config",
                return_value=[
                    {
                        "name": "worker",
                        "description": "worker agent",
                        "system_prompt": "worker prompt",
                    }
                ],
            ) as get_subagents,
            patch.object(
                acp_agent,
                "get_agent_skill_metadata",
                side_effect=lambda name: (
                    [
                        {
                            "name": "agent-creator",
                            "skill_path": ".deepagents/skills/agent-creator/SKILL.md",
                            "description": "Create Agents",
                        }
                    ]
                    if name == "worker"
                    else []
                ),
            ),
            patch.object(acp_agent, "StateBackend", return_value=object()),
            patch.object(acp_agent, "PowerShellBackend", return_value=object()),
            patch.object(acp_agent, "CompositeBackend", return_value=object()),
            patch.object(
                acp_agent,
                "_OverrideFilesystemMiddleware",
                side_effect=filesystem_middleware,
            ),
            patch.object(acp_agent, "create_model", return_value=object()),
            patch.object(acp_agent, "build_system_prompt", return_value="system"),
            patch.object(acp_agent, "create_deep_agent", return_value="compiled") as create_deep_agent,
        ):
            result = acp_agent.build_agent(
                SimpleNamespace(cwd="C:\\workspace"),
                agent_name="Chat",
            )

        self.assertEqual(result, "compiled")
        create_deep_agent.assert_called_once()
        get_subagents.assert_called_once_with("chat")
        self.assertEqual([call.args[0] for call in get_mcp_tools.call_args_list], ["chat", "worker"])

        main_args = create_deep_agent.call_args.kwargs
        self.assertNotIn("skills", main_args)
        self.assertEqual(main_args["interrupt_on"], {"execute": False, "delete": True})
        self.assertEqual(filesystem_instances[1].options["tools"], ["read_file"])
        self.assertNotIn("read_file", main_args["middleware"][-1]._excluded)
        self.assertIn("write_file", main_args["middleware"][-1]._excluded)

        child = main_args["subagents"][0]
        self.assertNotIn("skills", child)
        self.assertIn("- agent-creator", child["system_prompt"])
        self.assertEqual(
            {tool.name for tool in child["tools"]},
            {"create_todo", "update_todo", "get_todo"},
        )
        self.assertEqual(filesystem_instances[0].options["tools"], ["write_file", "read_file"])
        child_exclusions = child["middleware"][-1]._excluded
        self.assertIn("read_file", child_exclusions)
        self.assertNotIn("write_file", child_exclusions)


if __name__ == "__main__":
    unittest.main()
