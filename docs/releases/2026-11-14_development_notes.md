# 2026-11-14 开发日志

## 变更概述

统一 Agent 能力装配，并完善上下文用量、压缩说明和调试展示。Agent YAML 现在是工具、技能和子 Agent 的配置来源；上下文统计统一拆分消息与固定开销，页面和输入框可查看分类 token 用量。

## Agent 与技能

- Chat 与 task 共用 Agent 构建流程，依据 `.agent.md` frontmatter 装配内置工具、MCP 工具、技能及子 Agent。
- 技能元数据由 Agent 配置解析并写入 system prompt，调试上下文可以追踪实际注入文本。
- Web Search 工具由开关控制；上下文统计使用 Agent 实际装配的工具对象和 schema。
- 修复无关联知识库时空字符串覆盖默认提示的问题。

## 上下文用量与查看

- `/api/context/usage` 和 `/api/context/current` 统一报告系统提示词、技能、工具、消息及总用量的 tokens 和上下文窗口占比。
- Overhead 的工具清单来自实际 Agent 工具装配结果；上下文 HTML 支持中文、日文、英文。
- 输入框上下文悬浮提示展示总量，并列出系统提示词、技能、工具、消息各自的 tokens 和占比。
- 手动压缩结果也返回压缩后的分类用量，供输入框提示更新。
- 文档说明自动压缩触发公式及配置语义；当前固定 `CONTEXT_COMPACT_OVERHEAD` 与动态调试页估算可能不同。

## 压缩质量与已知风险

- 当前 `.env` 使用 `CONTEXT_COMPACT_RATIO=0.75`、`CONTEXT_COMPACT_OVERHEAD=6000`、每块摘要提示目标 `3072` tokens、保留最近 6 轮。
- `SUMMARY_MAX_TOKENS` 是摘要 prompt 中的长度指令，并非模型 API 强制的最大输出 token 数。
- **部分摘要分块失败仍有信息遗漏风险**：当前实现可能只使用成功分块的摘要，随后移除全部早期原文。该风险已记录在上下文压缩设计文档，尚未在本次修复。
- 当前 `CONTEXT_COMPACT_OVERHEAD` 是固定值，不会按 Agent、角色和工具实时计算；上下文调试接口则会按实际 prompt 和工具 schema 估算。

## 验证

- Agent 构造及知识库提示测试：9 项通过。
- 上下文统计与本地化测试：6 项通过。
- Python 语法检查、`git diff --check`：通过。
- Web UI `npm run build`：通过；Vite 提示既有的大型 chunk 警告。
- Vue 全量类型检查仍报告已有类型错误，本次未处理。
