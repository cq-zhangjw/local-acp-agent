"""Terminal tool registry: 工具结果即最终答案，模型不再复述。

Terminal 工具的调用流程（见 docs/terminal-tool-design.md）：
  模型请求 -> tool_call -> 执行工具 -> 结果直接作为最终回复推前端（跳过"模型复述"一轮请求）

生效条件（两条都满足才直通）：
1. 工具名在本白名单中（静态声明）
2. 本轮只有一个工具调用（模型并行调了多个工具 -> 走正常流程，防止丢其它工具结果）
"""

# key 为 MCP 工具加载后的全名（见 mcp_loader.make_tool: mcp_<server>_<tool>），
# 或内置工具名。value 支持 format 模板（{result} 为工具返回文本）。
TERMINAL_TOOLS: dict[str, dict] = {
    "mcp_calc_server_calculate": {"format": "结果：{result}"},
    # 后续扩展：时间工具、注音工具等（结果短小、确定性的才适合）
}


def is_terminal(tool_name: str) -> bool:
    """判断工具是否声明为 terminal（结果即最终答案）。"""
    return tool_name in TERMINAL_TOOLS


def format_terminal_result(tool_name: str, result: str) -> str:
    """按工具配置的模板格式化结果（无模板时原样返回）。"""
    fmt = TERMINAL_TOOLS.get(tool_name, {}).get("format")
    if not fmt:
        return result
    try:
        return fmt.format(result=result)
    except Exception:
        return result
