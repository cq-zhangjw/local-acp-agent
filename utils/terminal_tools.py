"""Terminal tool registry: 工具结果即最终答案，模型不再复述。

配置文件：项目根目录 terminal_tools.json（与 model_config.json 同级），每次调用
实时读取——改配置即时生效，无需重启后端。结构：

    {
      "mcp_calc_server_calculate": {"format": "{result}"},
      "<工具全名>": {"format": "<模板，{result} 为工具返回值，可省略>"}
    }

- MCP 工具全名：mcp_<server>_<tool>（见 mcp_loader.make_tool）
- 内置工具：直接使用工具名（如 "execute"）

Terminal 工具的调用流程（见 docs/terminal-tool-design.md）：
  模型请求 -> tool_call -> 执行工具 -> 结果直接作为最终回复推前端（跳过"模型复述"一轮请求）

生效条件（两条都满足才直通）：
1. 工具名在配置白名单中（静态声明）
2. 本轮只有一个工具调用（模型并行调了多个工具 -> 走正常流程，防止丢其它工具结果）
"""

import json
from pathlib import Path

TERMINAL_TOOLS_PATH = Path(__file__).resolve().parent.parent / "terminal_tools.json"


def _load_terminals() -> dict[str, dict]:
    """读取白名单配置（每次实时读，配置改动即时生效）。"""
    try:
        with open(TERMINAL_TOOLS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_terminal(tool_name: str) -> bool:
    """判断工具是否声明为 terminal（结果即最终答案）。"""
    return tool_name in _load_terminals()


def format_terminal_result(tool_name: str, result: str) -> str:
    """按工具配置的模板格式化结果（无模板时原样返回）。"""
    fmt = _load_terminals().get(tool_name, {}).get("format")
    if not fmt:
        return result
    try:
        return fmt.format(result=result)
    except Exception:
        return result
