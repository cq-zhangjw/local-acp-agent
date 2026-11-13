# Agent 单文件配置改造方案

状态：已实施  
日期：2026-11-13

## 1. 目标

废除 `.deepagents/agents/agents.json`，将每个 Agent 的结构化配置和行为提示词统一存储在一个
`.deepagents/agents/<agent-id>.agent.md` 文件中。

- 文件名（不含 `.agent.md`）是稳定的 Agent ID。
- YAML frontmatter 保存模型、Skill、Tool、知识库和子 Agent 引用。
- Markdown 正文是 Agent 的 system prompt。
- Skill、MCP manifest、模型连接信息仍由各自资源文件管理，Agent 文件只保存引用。

## 2. 文件格式

```markdown
---
schema: 1
description: 通用任务助手
enabled: true
model: qwen3.6-14b-a3a
skills:
  - code-review
tools:
  - read_file
  - edit_file
  - execute
  - calc_server/calculate
knowledge:
  - db_project_docs_1234
  top_k: 5
  threshold: 0.6
subagents:
  - reviewer
---

# 工作流程

这里是 Agent 的行为提示词。
```

### 2.1 标识规则

- Agent ID 只取自文件名，YAML 不重复保存 `name`。
- ID 继续使用 `[A-Za-z0-9_-]`，最长 64 个字符，以兼容现有 Agent。
- 展示名称如有需要使用可选字段 `title`，修改 `title` 不改变 Agent ID。

### 2.2 引用规则

- `model` 只保存 `model_config.json` 中的模型名，不保存 endpoint 或 API key。
- `skills` 只保存 `.deepagents/skills/<name>` 的目录名。
- `tools` 是统一字符串数组。
- 内置工具直接保存工具名，例如 `read_file`。
- MCP 工具保存为 `<server>/<tool>`，例如 `calc_server/calculate`。
- MCP 工具描述、输入 schema 等始终从对应 `manifest.json` 读取，不复制到 Agent 文件。
- `knowledge` 保存知识库 ID 数组；只要数组非空，运行时会保证
  `knowledge_server/retrieve_knowledge` 可用，并把允许访问的 ID 写入 Agent 提示词。
- `subagents` 只保存其他 Agent ID。

## 3. 运行时架构

新增统一的 Agent Registry：

```text
.agent.md
  -> YAML safe_load + Markdown 拆分
  -> 结构和引用规范化
  -> Agent Registry
  -> skill_loader / mcp_loader / context / manage API
```

所有调用方必须通过 Registry 读取配置，不得直接解析 Agent 文件。Registry 提供：

- Agent 扫描与按 ID 读取；
- YAML frontmatter 安全解析；
- 原子写入；
- 基于文件名、mtime 和大小的缓存失效；
- 旧 `agents.json` 一次性迁移；
- 向现有 API 暴露兼容结构，降低前端改造风险。

## 4. 管理行为

- Agent 新建、编辑和删除只操作对应 `.agent.md`。
- Skill、Tool、知识库开关修改当前 Agent 文件的 YAML frontmatter。
- 删除 Skill 或 MCP server 时，扫描所有 Agent 文件并清理引用。
- 工具管理 API 从 manifest 返回展示 metadata；Agent API 和持久化文件只保留紧凑引用。
- 配置写入使用临时文件后原子替换，防止中途失败产生半文件。

## 5. 迁移

升级时如果发现 `.deepagents/agents/agents.json`：

1. 读取旧配置。
2. 将每个 Agent 的配置与同名 `.agent.md` 正文合并。
3. 把 MCP 完整条目压缩为 `server/tool` 字符串引用。
4. 原子写回各 `.agent.md`。
5. 将旧文件改名为 `agents.json.migrated.bak`，运行时不再读取。

迁移完成后不得双写 JSON 和 Markdown。Git 管理的项目可在验证后删除迁移备份。

## 6. 兼容与错误处理

- 缺少 `skills` 表示使用全部已安装 Skill；显式空数组表示不启用 Skill。
- 缺少 `tools` 表示使用默认内置工具且不启用 MCP。
- YAML 非法、frontmatter 不是对象、文件内 `name` 与文件名冲突时明确报错，不静默降级。
- 不存在的 Skill、Tool、知识库或子 Agent 由 validate API 报告。
- YAML 解析使用 `yaml.safe_load`，禁止构造任意 Python 对象。

## 7. 验证

- Registry：解析、回写、Unicode、嵌套 Tool 配置、非法 YAML、缓存失效。
- 迁移：旧 JSON 的 Skill、MCP tool 子集、子 Agent 和正文均保留。
- 管理 API：Agent CRUD、Skill/Tool toggle、删除引用清理。
- 装配：Skill 和 MCP loader 读取同一 Agent 文件。
- 上下文页面：工具统计从 Registry 与 manifest 生成。
- 前端：现有 Agent 管理、Skill/Tool/知识库开关和生产构建通过。

