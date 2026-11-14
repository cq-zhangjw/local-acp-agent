import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.tools import StructuredTool  # noqa: E402

import acp_agent  # noqa: E402
import api.context as context_api  # noqa: E402


def _tool(name: str, description: str = "Test tool") -> StructuredTool:
    def run(value: str = "") -> str:
        return value

    return StructuredTool.from_function(func=run, name=name, description=description)


class AgentToolInventoryTests(unittest.TestCase):
    def test_inventory_matches_allowlist_and_web_search_toggle(self) -> None:
        search = _tool("mcp_browser_server_search_web", "Search the web")
        fetch = _tool("mcp_browser_server_fetch_webpage", "Fetch a webpage")
        read_file = _tool("read_file")
        execute = _tool("execute")
        task = _tool("task")

        def filesystem_middleware(*, backend, tools):
            available = {"read_file": read_file, "execute": execute}
            return SimpleNamespace(tools=[available[name] for name in tools])

        agents = {"coder": {"tools": {"inner_tools": ["read_file", "execute", "task"]}}}
        with (
            patch.object(acp_agent, "load_agents_config", return_value=agents),
            patch.object(acp_agent, "_create_agent_backend", return_value=object()),
            patch.object(acp_agent, "_OverrideFilesystemMiddleware", side_effect=filesystem_middleware),
            patch.object(acp_agent, "get_agent_mcp_tools", return_value=[search, fetch]),
            patch.object(acp_agent, "get_mcp_tool", return_value=search),
            patch.object(acp_agent, "get_agent_subagents_config", return_value=[]),
            patch.object(
                acp_agent,
                "_task_tool_metadata",
                return_value=task,
            ) as task_tool_metadata,
        ):
            disabled = acp_agent.get_agent_tool_inventory("coder", web_search=False)
            enabled = acp_agent.get_agent_tool_inventory("coder", web_search=True)

        self.assertEqual(
            {tool.name for tool, _ in disabled},
            {"read_file", "execute", "mcp_browser_server_fetch_webpage", "task"},
        )
        self.assertEqual(
            {tool.name for tool, _ in enabled},
            {
                "read_file",
                "execute",
                "mcp_browser_server_search_web",
                "mcp_browser_server_fetch_webpage",
                "task",
            },
        )
        task_tool_metadata.assert_called()
        self.assertEqual(
            task_tool_metadata.call_args.args[0][-1]["name"],
            "general-purpose",
        )

    def test_inventory_omits_task_when_agent_policy_excludes_it(self) -> None:
        read_file = _tool("read_file")
        task = _tool("task")
        agents = {"chat": {"tools": {"inner_tools": ["read_file"]}}}

        with (
            patch.object(acp_agent, "load_agents_config", return_value=agents),
            patch.object(acp_agent, "_create_agent_backend", return_value=object()),
            patch.object(
                acp_agent,
                "_OverrideFilesystemMiddleware",
                return_value=SimpleNamespace(tools=[read_file]),
            ),
            patch.object(acp_agent, "get_agent_mcp_tools", return_value=[]),
            patch.object(acp_agent, "get_agent_subagents_config", return_value=[]),
            patch.object(
                acp_agent,
                "_task_tool_metadata",
                return_value=task,
            ),
        ):
            inventory = acp_agent.get_agent_tool_inventory("chat")

        self.assertEqual([tool.name for tool, _ in inventory], ["read_file"])


class ContextAccountingTests(unittest.TestCase):
    def test_overhead_counts_prompt_skills_and_actual_tool_schema(self) -> None:
        tool = _tool("selected_tool", "A selected tool")
        skill_prompt = "- one-skill\n  - skill_path: path/SKILL.md"
        prompt = (
            "System instructions\n\n"
            "# Available Skills\n"
            f"{skill_prompt}\n\n"
            "# User preset\nNo role preset.\n"
        )
        with (
            patch.object(context_api._acp, "build_system_prompt", return_value=prompt),
            patch.object(context_api._acp, "_agent_skills_prompt", return_value=skill_prompt),
            patch.object(
                context_api._acp,
                "get_agent_tool_inventory",
                return_value=[(tool, "builtin")],
            ) as get_inventory,
        ):
            overhead = context_api._overhead_breakdown("coder", web_search=True)

        get_inventory.assert_called_once_with("coder", True)
        self.assertEqual(overhead["tool_count"], 1)
        self.assertEqual(overhead["skills"]["preview"], f"# Available Skills\n{skill_prompt}")
        self.assertEqual(
            overhead["system_prompt"]["tokens"] + overhead["skills"]["tokens"],
            overhead["prompt_tokens"],
        )
        self.assertEqual(
            overhead["total_tokens"],
            overhead["prompt_tokens"] + overhead["tool_tokens"],
        )
        self.assertEqual([row["name"] for row in overhead["tools"]], ["selected_tool"])

    def test_usage_is_messages_plus_overhead_and_uses_selected_model_size(self) -> None:
        with (
            patch.object(context_api, "sc_get_context", return_value=[]),
            patch.object(
                context_api,
                "_overhead_breakdown",
                return_value={
                    "system_prompt": {"tokens": 50},
                    "skills": {"tokens": 25},
                    "tool_tokens": 50,
                    "total_tokens": 125,
                },
            ),
            patch.object(context_api, "_content_size_for_model", return_value=1000),
        ):
            usage = asyncio.run(context_api._usage("session", "coder", model_name="large"))

        self.assertEqual(usage["message_tokens"], 0)
        self.assertEqual(usage["overhead_tokens"], 125)
        self.assertEqual(usage["tokens"], 125)
        self.assertEqual(usage["total"], 1000)
        self.assertEqual(usage["pct"], 12)
        self.assertEqual(usage["categories"]["messages"]["tokens"], 0)
        self.assertEqual(usage["categories"]["total"]["tokens"], 125)

    def test_context_report_total_is_the_sum_of_all_categories(self) -> None:
        message = SimpleNamespace(
            content="hello",
            tool_calls=[],
            id="message-1",
        )
        overhead = {
            "system_prompt": {"tokens": 20},
            "skills": {"tokens": 10},
            "tool_tokens": 30,
            "total_tokens": 60,
        }
        compaction_plan = {
            "threshold": 100,
            "would_compact": False,
            "would_summarize": 0,
            "would_summarize_tokens": 0,
            "would_keep": 1,
            "keep_turns": 1,
        }
        with (
            patch.object(context_api, "sc_get_context", return_value=[{}]),
            patch.object(context_api, "convert_to_messages", return_value=[message]),
            patch.object(context_api, "estimate_tokens", return_value=40),
            patch.object(context_api, "estimate_text_tokens", return_value=5),
            patch.object(context_api, "_overhead_breakdown", return_value=overhead),
            patch.object(context_api, "_content_size_for_model", return_value=1000),
            patch.object(context_api, "active_config", return_value={"ratio": 0.8}),
            patch.object(context_api, "next_compaction_plan", return_value=compaction_plan),
            patch("utils.context_compactor.split_compact", return_value=([], [message])),
        ):
            report = context_api._build_context_report(
                "session",
                "coder",
                500,
                model_name="large",
            )

        self.assertEqual(report["stats"]["tokens"], 100)
        self.assertEqual(report["stats"]["pct"], 10)
        self.assertEqual(
            sum(
                category["tokens"]
                for name, category in report["stats"]["categories"].items()
                if name != "total"
            ),
            report["stats"]["tokens"],
        )

    def test_context_html_uses_requested_locale_and_shows_all_categories(self) -> None:
        report = {
            "ok": True,
            "lang": "en",
            "session_id": "session",
            "agent": "coder",
            "config": {"ratio": 0.8, "overhead": 6000, "summary_budget_tokens": 1000},
            "plan": {
                "threshold": 100,
                "would_compact": False,
                "would_summarize": 1,
                "would_summarize_tokens": 20,
                "would_keep": 2,
                "keep_turns": 1,
            },
            "summary": {"long": "", "recent": "", "tokens": 0},
            "stats": {
                "message_count": 1,
                "tokens": 100,
                "pct": 10,
                "content_size": 1000,
                "categories": {
                    "system_prompt": {"tokens": 20, "pct": 2},
                    "skills": {"tokens": 10, "pct": 1},
                    "tools": {"tokens": 30, "pct": 3},
                    "messages": {"tokens": 40, "pct": 4},
                    "total": {"tokens": 100, "pct": 10},
                },
            },
            "overhead": {
                "total_tokens": 60,
                "tool_count": 1,
                "tool_tokens": 30,
                "system_prompt": {"tokens": 20, "preview": "System"},
                "skills": {"tokens": 10, "preview": "Skills"},
                "tools": [],
            },
            "messages": [],
        }

        html = context_api._html_context_report(report)

        self.assertIn("Current context", html)
        self.assertIn("System prompt", html)
        self.assertIn("Skills", html)
        self.assertIn("Tools", html)
        self.assertIn("Messages", html)
        self.assertIn("100 / 1000 tokens", html)
        for percentage in ("2%", "1%", "3%", "4%"):
            self.assertIn(percentage, html)

        report["lang"] = "ja"
        localized_html = context_api._html_context_report(report)
        self.assertIn("現在のコンテキスト", localized_html)
        self.assertIn("システムプロンプト", localized_html)

        report["lang"] = "zh"
        localized_html = context_api._html_context_report(report)
        self.assertIn("当前上下文", localized_html)
        self.assertIn("系统提示词", localized_html)
        self.assertIn("消息列表", localized_html)


if __name__ == "__main__":
    unittest.main()
