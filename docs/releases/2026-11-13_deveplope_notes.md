# 2026-11-13 开发日志

## 变更概述

本次开发主要完善前端明暗主题体系，并对聊天页面、Markdown 渲染、快捷指令、侧栏滚动条和多语言支持进行了集中优化。

## 功能变更

### 1. 明暗主题切换

- 新增 `light`、`dark`、`system` 三种主题偏好。
- 在页面顶部菜单中增加主题切换入口。
- 使用 `localStorage` 持久化用户选择，存储键为 `acp-ui-theme`。
- 支持跟随操作系统主题变化。
- 通过 `BroadcastChannel` 和 `storage` 事件同步多个窗口的主题状态。
- 首屏加载时提前应用主题，减少页面闪烁。
- 将当前主题同步到 Naive UI、CSS Color Scheme 和全局 CSS Token。
- 同步适配语音通话窗口。

### 2. 深色主题视觉适配

- 修复聊天页面局部 CSS 变量覆盖全局主题 Token，导致深色模式下大部分区域仍显示浅色的问题。
- 完成以下区域的深色适配：
  - 页面画布和顶部栏
  - 左侧会话目录
  - 会话分组标题、数量徽标和折叠图标
  - 消息卡片和输入区域
  - 快捷指令卡片和弹窗
  - Markdown 内容
  - 工具调用卡片
  - Agent 执行过程
  - 知识库管理弹窗
  - Mermaid 图表

### 3. 代码高亮主题切换

- 引入 Highlight.js 官方 `github.css` 和 `github-dark.css`。
- 根据当前明暗主题动态更新代码高亮样式。
- Mermaid 缓存增加主题维度，避免主题切换后继续显示旧主题图表。

### 4. 快捷指令优化

- Chat 模式下允许选择模型。
- 新建 Chat 快捷指令时默认选择模型列表第一项。
- Task 模式继续保留“默认模型”选项。
- 从 Task 切换到 Chat 时自动补充可用模型。
- 编辑旧版未指定模型的 Chat 快捷指令时自动选择首个模型。
- 快捷指令卡片仅显示以下状态：
  - 模式
  - 模型
  - 联网搜索
  - 历史上下文
  - 角色预设
- 联网搜索、历史上下文和角色预设改为仅在启用或已设置时显示图标。

### 5. 滚动条样式统一

- 优化左侧会话目录滚动条。
- 与消息区域统一为 6px 宽度、透明轨道和圆角滑块。
- 隐藏滚动条上下按钮。
- 增加 Firefox 兼容样式。

### 6. 多语言完善

- 补充中文、日文和英文语言包。
- 完成以下区域的动态翻译：
  - 日语注音按钮、提示和无障碍标签
  - 角色预设入口
  - 角色搜索和角色编辑器
  - 角色启用、保存提示
  - 快捷指令新建、编辑和删除确认
  - 快捷指令表单标签、占位符和状态提示
  - 模式、模型和角色选项
- 将快捷指令选项改为响应式计算值，语言切换后立即更新。
- 修正日文语言包中的中日混杂文案：
  - `定时タスク` 调整为 `定期タスク`
- 全局扫描 Vue 模板，清理用户可见的中日文硬编码。

## 主要技术实现

- 新增统一主题模块：`web/src/theme.ts`。
- 在 `web/src/App.vue` 中接入 Naive UI 明暗主题。
- 在 `web/src/style.css` 中定义统一的 `--ui-*` 主题 Token。
- 在 `web/index.html` 中增加首屏主题初始化。
- 更新聊天页、语音页、Markdown、工具卡片和快捷指令等组件，使其统一消费主题 Token。
- 更新 `zh.ts`、`ja.ts`、`en.ts` 三套语言资源。

## 主要修改文件

- `web/index.html`
- `web/src/App.vue`
- `web/src/theme.ts`
- `web/src/style.css`
- `web/src/pages/ChatPage.vue`
- `web/src/pages/VoiceCallPage.vue`
- `web/src/components/MarkdownMessage.vue`
- `web/src/components/ExecutionProcess.vue`
- `web/src/components/ToolCallCard.vue`
- `web/src/components/QuickCommandBar.vue`
- `web/src/components/KnowledgeManageModal.vue`
- `web/src/locales/zh.ts`
- `web/src/locales/ja.ts`
- `web/src/locales/en.ts`
- `docs/theme-switching-plan.md`

## 验证结果

- `npx vue-tsc --noEmit`：通过。
- `npm run build`：通过。
- 已重新生成 `web/dist/` 生产构建资源。
- 已在实际页面验证明暗主题、代码高亮、滚动条、快捷指令和多语言切换效果。
- Vue 模板可见文本、`title`、`aria-label`、`placeholder` 及消息提示的中日文硬编码扫描无残留。

## 已知事项

- Vite 构建仍会提示部分 Chunk 大于 500 kB，该警告为项目现有问题，本次修改未新增相关构建错误。
- 统计弹窗的 ECharts 按需 Chunk 约 740 kB（gzip 约 228 kB），已通过异步组件隔离，不进入聊天页面首屏主包。

## 使用统计功能

### 方案与数据模型

- 新增 [使用统计功能实施方案](../statistics-feature-plan.md)。
- 采用独立 SQLite 数据库 `db/statistics.sqlite3`，不污染会话历史和上下文数据。
- 单条记录对应一次用户提交，记录：
  - UTC 开始、完成时间
  - 本机机器名
  - Chat/Task 模式
  - Agent、角色和模型名称
  - 工具调用次数
  - AI 调用次数
  - 端到端耗时
  - 用户问题
  - 完成、失败、取消或中断状态
  - ACP Session ID 和前端会话 ID
- SQLite 启用 WAL、`busy_timeout` 和必要索引，支持多个 Agent 子进程并发写入。
- 支持通过环境变量控制数据库路径、机器名、问题正文存储和数据保留时间。

### 执行指标采集

- 在 `RollbackAgentServer.prompt` 的完整执行边界中创建和完成统计记录。
- 使用 `ContextVar` 隔离并发请求的统计计数器。
- 通过 LangChain 回调统计真实模型执行和工具执行：
  - `on_chat_model_start` 统计 AI 调用。
  - `on_tool_start` 统计实际工具调用。
- 模型与工具回调可向子 Agent 传播。
- 未实际请求模型的 Terminal 工具结果直通不会误计为 AI 调用。
- 统计写入失败只记录日志，不会中断正常 Chat/Task 请求。
- 应用启动时将异常退出遗留的 `running` 记录恢复为 `interrupted`。
- 修复 Windows 下 SQLite 连接上下文仅提交但未关闭文件句柄的问题，所有连接现在均在 `finally` 中可靠关闭。

### API

新增以下接口：

- `GET /api/statistics/records`
  - 支持时间、机器名、模式、Agent、角色、模型、状态和问题关键词筛选。
  - 支持分页。
- `GET /api/statistics/summary`
  - 返回请求数、Chat/Task 数、工具/AI 调用总数、平均耗时、P95 耗时、失败率和每日趋势。
- `GET /api/statistics/options`
  - 返回筛选器所需的机器名、Agent、角色、模型和状态选项。
- `GET /api/statistics/export.csv`
  - 按当前筛选条件导出明细。
  - 对公式前缀进行转义，防止 CSV 公式注入。

### 前端统计界面

- 在聊天页面右上角“更多操作”菜单中新增“使用统计”入口。
- 新增统计弹窗，包含：
  - 时间、模式、机器名、Agent、角色、模型、状态和问题关键词筛选。
  - 请求总数、Chat/Task 数量、工具/AI 调用总数、平均耗时、P95 耗时和失败率指标卡。
  - 请求、AI 调用和工具调用的每日趋势图。
  - 执行明细表和分页。
  - CSV 导出。
- 统计明细表新增模型名列；CSV 同步保留 `model_name` 字段。
- 未显式传递模型名时，后端使用实际解析后的默认模型名落库，避免记录为空。
- 完成中文、日文和英文翻译。
- 完成浅色、深色主题适配。
- 使用项目现有 ECharts，并按需注册图表组件。
- 统计弹窗改为异步加载，ECharts 独立分包，不增加聊天页面首屏主包体积。
- WebSocket 显式传递 `mode=chat|task`，Prompt 元数据增加前端会话 ID 和原始用户问题。

### 测试与验证

- 统计功能测试共 8 项，全部通过：
  - 自动建表和统计查询。
  - 汇总、P95 和趋势。
  - 多线程并发写入。
  - 中断记录恢复。
  - 关键词通配符转义。
  - CSV 公式注入防护。
  - FastAPI 明细、汇总和导出接口。
  - LangChain 模型及工具真实回调计数。
- `npx vue-tsc --noEmit`：通过。
- `npm run build`：通过。
- 实际页面验证：
  - Chat/Task 示例数据正确展示。
  - 机器名、角色、AI/工具调用次数和耗时正确。
  - 趋势图正常生成。
  - 中文和英文正确渲染。
  - 深色模式指标卡背景和文字正确。

### 登录账户统计

- SQLite 统计表新增 `user_name` 字段，与 `machine_name` 分开保存。
- Windows 登录账户由后端组合 `USERDOMAIN` 和 `USERNAME`，格式示例：
  `G08\cq.zhangjw`。
- 为旧版 `statistics.sqlite3` 增加自动迁移：
  - 检测 `user_name` 列是否存在。
  - 先增加字段，再创建 `(user_name, started_at)` 索引。
  - 旧记录保持为空，避免错误回填为当前登录用户。
- 明细、汇总筛选、筛选选项和 CSV 导出均支持登录账户。
- 统计弹窗新增“登录账户”筛选器和明细列。
- 补充中文、日文和英文文案。
