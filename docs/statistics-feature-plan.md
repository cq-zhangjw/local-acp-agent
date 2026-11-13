# 使用统计功能实施方案

## 1. 目标

为每次交互式 Chat/Task 请求建立一条可查询的执行统计记录，用于分析使用量、Agent/角色使用情况、工具和 AI 调用次数，以及端到端耗时。

单条记录至少包含：

- 时间
- 登录账户（Windows 域账户）
- 机器名
- 模式（Chat/Task）
- Agent 名
- 角色名
- 工具调用回数
- AI 调用回数
- 总耗时
- 用户问题

第一版只统计用户从聊天页面发起的交互式 Chat/Task 请求。定时任务通过 `source` 字段预留，暂不混入人工使用统计。

## 2. 统计口径

### 2.1 记录粒度

用户每提交一次 `session/prompt`，创建一条统计记录。重试视为新的执行记录，并通过 `is_retry` 标识。

### 2.2 开始与结束

- 开始：Agent 后端收到 `session/prompt`。
- 完成：最终结果生成完成。
- 失败：执行抛出异常。
- 取消：用户主动取消。
- 中断：进程退出后仍处于 `running` 的遗留记录。

### 2.3 工具调用回数

统计实际开始执行的工具次数：

- 成功或失败的工具执行均计数。
- 同一工具重试两次计为两次。
- 并行执行三个工具计为三次。
- 只生成工具调用但未获授权、未开始执行时不计数。
- 子 Agent 内部执行的工具计入总数。

### 2.4 AI 调用回数

统计实际进入模型执行链的次数，包括：

- 主 Agent 模型调用。
- 工具结果返回后的继续推理。
- 子 Agent 模型调用。
- 当前请求触发的上下文压缩模型调用。

未实际调用模型的 Terminal 工具结果直通不计数。

### 2.5 总耗时

使用 `time.perf_counter()` 计算端到端耗时，包含上下文处理、模型调用、工具执行和权限等待，单位为毫秒。

## 3. 数据库设计

使用独立 SQLite 数据库：

```text
db/statistics.sqlite3
```

选择 SQLite 的原因：

- Python 标准库内置，无需新增依赖。
- 支持时间范围、分组、分页和聚合查询。
- WAL 模式适合网关和多个 Agent 子进程并发读写。
- 便于实现 CSV 导出和后续数据保留策略。

### 3.1 表结构

```sql
CREATE TABLE IF NOT EXISTS execution_statistics (
    id                  TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    machine_name        TEXT NOT NULL,
    user_name           TEXT,
    mode                TEXT NOT NULL CHECK (mode IN ('chat', 'task')),
    agent_name          TEXT NOT NULL,
    role_name           TEXT,
    model_name          TEXT,
    tool_call_count     INTEGER NOT NULL DEFAULT 0,
    ai_call_count       INTEGER NOT NULL DEFAULT 0,
    duration_ms         INTEGER,
    question            TEXT NOT NULL,
    status              TEXT NOT NULL,
    error_type          TEXT,
    session_id          TEXT NOT NULL,
    conversation_id     TEXT,
    source              TEXT NOT NULL DEFAULT 'interactive',
    is_retry            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);
```

索引覆盖：

- `started_at`
- `(machine_name, started_at)`
- `(user_name, started_at)`
- `(mode, started_at)`
- `(agent_name, started_at)`
- `status`

SQLite 连接启用：

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 10000;
PRAGMA synchronous = NORMAL;
```

## 4. 后端实现

### 4.1 存储模块

新增 `utils/statistics_store.py`：

- 自动创建数据库和表。
- 读取并缓存机器名。
- 创建 `running` 记录。
- 完成、失败或取消时更新记录。
- 清理或标记遗留 `running` 记录。
- 提供明细、汇总、筛选选项和趋势查询。

机器名读取顺序：

1. `STATISTICS_MACHINE_NAME`
2. `COMPUTERNAME`
3. `platform.node()`
4. `socket.gethostname()`

登录账户由后端组合 `USERDOMAIN` 和 `USERNAME`，例如
`G08\cq.zhangjw`。登录账户与机器名分别存储，避免公共机器上的不同用户
被合并统计。

### 4.2 请求级统计上下文

新增 `utils/statistics_context.py`，使用 `contextvars.ContextVar` 保存当前请求的计数器，避免并发会话互相污染。

计数器字段：

- `record_id`
- `ai_call_count`
- `tool_call_count`

### 4.3 Agent 接入

在 `RollbackAgentServer.prompt` 中：

1. 提取用户问题文本。
2. 创建 `running` 统计记录。
3. 激活请求级统计上下文。
4. 执行原有 Prompt 流程。
5. 在 `finally` 中写入最终次数、状态和耗时。

通过 LangChain 异步回调：

- `on_chat_model_start`：AI 调用数加一。
- `on_tool_start`：工具调用数加一。

回调通过 Agent 的运行配置传递，并向子 Agent 传播。

### 4.4 WebSocket 元数据

前端 WebSocket 增加：

```text
mode=chat|task
```

网关向 Agent 子进程传递：

```text
RUN_MODE
AGENT_NAME
ROLE_NAME
MODEL_NAME
MACHINE_NAME
```

前端发送 `session/prompt` 时，在 `_meta` 中增加 `conversation_id`，使统计记录可关联前端会话。

## 5. 查询 API

新增 `api/statistics.py` 并注册到 FastAPI。

### 5.1 明细

```http
GET /api/statistics/records
```

支持：

- 起止时间
- 机器名
- Chat/Task 模式
- Agent
- 角色
- 状态
- 用户问题关键词
- 页码和每页数量

### 5.2 汇总

```http
GET /api/statistics/summary
```

返回：

- 请求总数
- 完成、失败、取消数量
- Chat/Task 数量
- 工具调用总数
- AI 调用总数
- 平均耗时
- P95 耗时
- 每日趋势

### 5.3 筛选选项

```http
GET /api/statistics/options
```

返回机器名、Agent、角色、模型和状态的去重列表。

### 5.4 CSV 导出

```http
GET /api/statistics/export.csv
```

使用与明细列表相同的筛选条件。

## 6. 前端界面

在聊天页面右上角“更多操作”菜单新增“使用统计”。

统计界面使用大尺寸弹窗，包含：

### 6.1 筛选区

- 时间范围
- 模式
- 机器名
- 登录账户
- Agent
- 角色
- 模型
- 状态
- 问题关键词
- 查询、重置、CSV 导出

### 6.2 指标卡

- 请求总数
- Chat 数量
- Task 数量
- 工具调用总数
- AI 调用总数
- 平均耗时
- P95 耗时
- 失败率

### 6.3 趋势图

使用项目现有 ECharts：

- 每日请求数
- 每日 AI 调用数
- 每日工具调用数

### 6.4 明细表

显示：

- 时间
- 机器名
- 模式
- 登录账户
- Agent
- 角色
- 模型
- 工具调用
- AI 调用
- 总耗时
- 状态
- 用户问题

用户问题默认省略显示，悬停或点击查看完整内容。

界面同时支持：

- 浅色/深色主题
- 中文、日文、英文
- 分页
- 加载与错误状态

## 7. 环境配置

```env
STATISTICS_ENABLED=true
STATISTICS_DB_PATH=db/statistics.sqlite3
STATISTICS_STORE_QUESTION=true
STATISTICS_RETENTION_DAYS=365
STATISTICS_MACHINE_NAME=
```

- `STATISTICS_ENABLED`：是否启用采集。
- `STATISTICS_DB_PATH`：SQLite 文件路径。
- `STATISTICS_STORE_QUESTION`：是否保存问题正文。
- `STATISTICS_RETENTION_DAYS`：保留天数，`0` 表示永久保存。
- `STATISTICS_MACHINE_NAME`：可选机器名覆盖值，主要用于测试。

## 8. 隐私与可靠性

- 机器名和问题正文由本地后端采集，不接受前端伪造。
- 问题正文不保存附件 Base64 数据。
- 禁用问题正文存储时保存空字符串，不影响其他指标。
- 查询参数全部参数化，禁止拼接 SQL。
- CSV 对以 `=`, `+`, `-`, `@` 开头的单元格进行转义，防止表格公式注入。
- 统计失败只记录日志，不得影响正常 Chat/Task 执行。

## 9. 测试与验收

### 9.1 后端

- 自动建表和索引。
- 多进程/多线程并发写入。
- 正常、失败、取消和中断状态。
- Chat/Task、Agent、角色和机器名写入。
- AI/工具调用计数。
- 时间、条件和关键词筛选。
- 汇总、P95 和趋势。
- CSV 转义。

### 9.2 前端

- 三语言渲染。
- 浅色/深色主题。
- 筛选、分页和刷新。
- 空数据、错误和加载状态。
- CSV 导出。

### 9.3 构建

```powershell
python -m unittest tests.test_statistics
Set-Location web
npx vue-tsc --noEmit
npm run build
```

## 10. 实施顺序

1. SQLite 存储与数据模型。
2. Agent 执行指标采集。
3. 查询和导出 API。
4. 前端统计弹窗。
5. 三语言和主题适配。
6. 后端测试、类型检查和生产构建。
7. 更新 `docs/releases/` 开发日志。
