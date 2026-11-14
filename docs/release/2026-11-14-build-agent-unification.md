# build_agent 统一构造

## 背景

Chat 与 task 原先分别调用 `create_deep_agent`，导致 Chat 绕过 Agent YAML 中配置的 MCP 工具、技能和子 Agent。Agent 的能力应由对应 `.agent.md` 的 YAML frontmatter 决定。

## 改动

- 合并 `build_agent` 的构造流程，Chat 与 task 共用工作区后端和一次 `create_deep_agent` 调用。
- 按 Agent frontmatter 装配 `tools.inner_tools`、`tools.mcp_tools`、`skills` 与 `subagents`；Agent 名称匹配不区分大小写。
- 文件系统工具采用 YAML allowlist；`task` 和 Todo 工具仅在工具清单配置时提供。配置了子 Agent 的 Agent 仍可按自身 frontmatter 独立配置技能与工具。
- 保留 `execute` / `delete` 的审批策略，并移除 Chat 专属的工具注入及屏蔽逻辑。
- 调整 Chat Agent 的提示说明，使其遵循 YAML 中声明的能力。

## 验证

- `python -m unittest discover -s tests`：26 项测试通过。
- `py_compile`：修改的 Python 文件编译通过。
- 使用 fake chat model 实际构建 DeepAgents 图，返回 `CompiledStateGraph`。
