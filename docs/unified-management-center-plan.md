# 统一设置与管理中心 UI 改造方案

> 状态：核心改造已实施，组件拆分与 API 语义优化持续推进  
> 日期：2026-11-13  
> 适用范围：`web/src/pages/ChatPage.vue` 及其管理相关组件  
> 关联文档：[UI_DESIGN.md](./UI_DESIGN.md)、[schedule_task.md](./schedule_task.md)、[single-agent-file-migration.md](./single-agent-file-migration.md)

## 1. 背景

当前管理能力分散在多个位置：

- 左侧导航直接提供“定时任务”和“Agent 管理”入口；
- 输入框工具栏提供“子 Agent / Skill / Tool / 知识库”入口；
- 右侧 300px 面板承载 Agent、Skill、Tool、知识库和定时任务；
- 知识库完整管理又打开独立弹窗；
- 角色预设编辑位于输入框区域；
- Agent、Skill、Tool 的资源管理和“当前 Agent 是否启用”混在同一界面。

这会产生三个问题：

1. 聊天输入区承担了管理职责，运行时操作与持久化配置边界不清晰。
2. 右侧窄面板不适合 Agent Prompt、MCP 工具列表、定时任务执行记录等复杂内容。
3. Skill、Tool、知识库开关实际修改 Agent 配置，但用户容易理解成只对当前会话生效。

## 2. 设计结论

### 2.1 建立统一“设置与管理中心”

采用一个应用级管理窗口：

- 左侧：固定管理菜单；
- 右侧：当前管理模块；
- 桌面端使用大尺寸居中窗口；
- 移动端使用全屏窗口；
- 关闭后回到原会话，不丢失输入内容、滚动位置和运行状态。

窗口名称建议使用 **“设置与管理”**，而不是单独叫“设置”。定时任务、Agent、Skill 和 Tool 都是业务资源管理，不是普通偏好设置。

第一阶段菜单：

```text
设置与管理
├─ 自动化
│  └─ 定时任务
├─ Agent
│  ├─ Agent 管理
│  ├─ Skill 管理
│  ├─ Tool 管理
│  └─ 知识库
└─ 应用
   ├─ 角色预设
   ├─ 快捷指令
   └─ 常规设置（预留）
```

后续可增加：

- 模型配置；
- 统计与诊断；
- 数据备份与导入导出。

### 2.2 管理入口收敛

左侧会话栏底部增加唯一入口：

```text
⚙ 设置与管理
```

移除：

- 左侧导航中的“定时任务”；
- 左侧导航中的“Agent 管理”；
- 输入框中的“子 Agent”；
- 输入框中的“Skill”；
- 输入框中的“Tool”；
- 输入框中的“知识库”；
- 整个右侧管理面板。

输入框只保留运行时操作：

- 附件；
- 当前 Agent 选择；
- 所有 Agent 共用的历史、联网、注音和角色选择；
- 模型选择；
- 上下文用量；
- 发送 / 取消。

Agent 选择器仍属于输入框，因为它决定当前运行实例。选择器可增加“查看能力”只读入口，但不能直接修改 Skill、Tool 或知识库关联。

### 2.3 资源管理与 Agent 关联分离

这是本次改造必须同步落实的交互原则：

| 页面 | 管理内容 | 不应提供 |
| --- | --- | --- |
| Agent 管理 | Agent 定义、Prompt、默认模型、关联 Skill、Tool、知识库、子 Agent | 不直接编辑 Skill / Tool 资源文件 |
| Skill 管理 | Skill 资源的新建、编辑、删除、查看引用 | “对当前 Agent 启用”开关 |
| Tool 管理 | MCP server 定义、工具清单、连接状态、编辑、删除 | “对当前 Agent 启用”开关 |
| 知识库 | 知识库资源、文档、服务状态 | “对当前 Agent 启用”开关 |
| 定时任务 | 任务定义、调度、执行 Agent、运行记录 | 修改 Agent 能力 |

Agent 与资源的关联统一在 **Agent 编辑页** 完成：

```text
编辑 Agent
├─ 基本信息
├─ Prompt
├─ Skills
├─ Tools
├─ 知识库
└─ 子 Agent
```

Skill、Tool 和知识库卡片可显示“被 N 个 Agent 使用”，点击后列出引用方，但不在资源卡片上修改关联。

“子 Agent”不再作为独立管理页面。它是 Agent 与 Agent 之间的关联，应合并到 Agent 编辑页。

## 3. 窗口布局

### 3.1 桌面端

建议窗口尺寸：

- 宽度：`min(1180px, 94vw)`；
- 高度：`min(820px, 90vh)`；
- 最小宽度：`900px`；
- 左侧菜单：`220px`；
- 右侧内容：剩余空间；
- 窗口内部独立滚动，页面背景不滚动。

```text
+--------------------------------------------------------------------------------+
| 设置与管理                                                   [关闭]             |
+----------------------+---------------------------------------------------------+
| 自动化               | Agent 管理                              12 个 Agent      |
|   定时任务            | [搜索 Agent........................]  [+ 新建 Agent]     |
|                      |---------------------------------------------------------|
| Agent                |                                                         |
|   Agent 管理          |  [Agent Card] [Agent Card] [Agent Card]                |
|   Skill 管理          |  [Agent Card] [Agent Card]                             |
|   Tool 管理           |                                                         |
|   知识库              |                                                         |
|                      |                                                         |
| 应用                 |                                                         |
|   角色预设            |                                                         |
+----------------------+---------------------------------------------------------+
```

实现建议：

- 外层：`NModal`；
- 内容容器：自定义 `ManagementCenter`；
- 左侧菜单：`NMenu`；
- 右侧：页面头部 + 内容滚动区；
- 编辑资源继续使用独立 `NModal`，避免列表页和复杂表单同时滚动。

不建议继续把管理内容做成聊天工作区第三栏。复杂管理页面需要稳定宽度，也不应挤压正在阅读的会话。

### 3.2 窄屏

当宽度小于 `900px`：

- 管理窗口全屏；
- 左侧菜单缩为顶部当前模块按钮；
- 点击后打开 `NDrawer` 选择管理模块；
- 卡片单列；
- 页面头部搜索框和追加按钮允许换行。

## 4. 统一页面头部

所有管理模块使用相同头部结构：

```text
[标题 + 数量 + 简短说明]
[搜索框................................] [筛选] [+ 追加]
```

规则：

- 页面头部在右侧内容区内 sticky；
- 搜索框实时过滤当前模块，不做跨模块全局搜索；
- 搜索状态按模块分别保存，切换菜单后再返回仍保留；
- “追加”按钮固定在搜索框右侧；
- 不支持追加的页面不显示空按钮；
- 筛选项较少时使用 `NSelect`，较多时使用筛选 Popover；
- 搜索、筛选和列表加载状态互不覆盖。

不建议把一个全局搜索框同时搜索 Agent、Skill、Tool 和定时任务，因为不同资源的字段和操作语义不同。

## 5. 卡片布局

### 5.1 Agent、Skill、Tool

按需求使用 Flex 换行布局：

```css
.resource-card-list {
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  gap: 16px;
}

.resource-card {
  flex: 1 1 280px;
  min-width: 260px;
  max-width: 360px;
}
```

响应规则：

- 大屏：每行 3 张；
- 中屏：每行 2 张；
- 小屏：每行 1 张；
- 最后一行卡片不强制拉满整个内容区；
- 卡片高度尽量一致，但描述过长时截断，不使用固定高度隐藏关键状态。

### 5.2 卡片通用结构

```text
+--------------------------------------+
| 图标  名称                  状态 Tag |
|       简短描述                       |
|                                      |
| 关联/数量/更新时间等摘要              |
|--------------------------------------|
| [编辑] [查看]                 [更多] |
+--------------------------------------+
```

通用规则：

- 点击卡片主体进入查看或编辑；
- 危险操作统一放入“更多”菜单；
- 删除必须二次确认；
- 默认 Agent、内置 Skill 等不可删除项显示锁定原因；
- loading 只锁定对应卡片，不阻塞整个页面；
- 空态提供说明和追加按钮；
- 搜索无结果与资源为空使用不同文案。

### 5.3 Agent 卡片

显示：

- Agent 名称、描述；
- `enabled` 状态；
- 当前使用标记；
- Skill / Tool / 知识库 / 子 Agent 数量；
- 配置文件名；
- 编辑、复制、删除。

不要直接铺满所有 Skill 和 Tool 标签。资源多时会使卡片高度失控，应显示：

```text
4 Skills · 46 Tools · 2 Knowledge Bases · 1 Sub Agent
```

Agent 编辑使用大尺寸表单，左侧为基本信息和 Prompt，右侧使用 Tabs 或分区管理关联资源。

### 5.4 Skill 卡片

显示：

- 名称、描述；
- 文件路径；
- 被多少 Agent 引用；
- 更新时间；
- 编辑、复制、删除。

Skill 页面不再显示当前 Agent 的启停开关。是否关联由 Agent 编辑页控制。

### 5.5 Tool 卡片

一个 MCP server 对应一张卡片，显示：

- server 名称和说明；
- 工具数量；
- 服务类型 / command；
- 服务状态；
- 被多少 Agent 引用；
- 查看工具、编辑配置、测试连接、删除。

点击“查看工具”打开详情弹窗，展示该 server 下的工具列表、描述和 schema。不要在卡片网格内展开全部工具，否则会破坏卡片布局。

内置工具不作为独立资源卡片管理。它们没有可编辑资源定义，只在 Agent 编辑页的 Tools 分区中作为可选项出现。

### 5.6 定时任务

定时任务不强制使用 Flex 卡片网格。它包含调度、下次执行、执行 Agent、状态和运行记录，更适合全宽列表卡片：

```text
[任务名称] [启用] [状态]
描述
执行 Agent · 调度 · 下次执行 · 上次执行
[立即执行] [编辑] [运行记录] [更多]
```

执行记录默认不在列表卡片中全部展开。点击“运行记录”打开详情抽屉或弹窗，避免任务列表过长。

点击任务绑定会话时：

1. 关闭设置与管理窗口；
2. 选择对应会话；
3. 如指定了执行记录，滚动并高亮对应消息。

## 6. 各模块详细设计

### 6.1 Agent 管理

列表页：

- 搜索名称、描述；
- 筛选 enabled / disabled；
- 新建 Agent；
- Flex 卡片列表。

编辑页：

- 基本信息：ID、描述、enabled；
- Prompt：Markdown 编辑 + 预览；
- Skills：复选框列表；
- Tools：`Built-in tools` 可折叠复选组，以及
  `mcp_server / 服务名 / 工具名` 三级可折叠复选树；
- 知识库：数据库复选框列表；
- 子 Agent：排除自身的 Agent 复选框列表；
- 保存前执行 validate；
- 保存失败保留表单状态并显示明确错误。

Agent ID 创建后不可直接修改。后续如需重命名，应提供专门迁移操作，同时更新其他 Agent、定时任务和会话引用。

### 6.2 Skill 管理

- 搜索名称和 description；
- 新建 Skill；
- Flex 卡片；
- 编辑器沿用 Markdown 编辑 + 预览；
- 删除前显示引用该 Skill 的 Agent；
- 被引用时允许删除，但必须明确提示将从哪些 Agent 移除；也可选择第一阶段直接阻止删除。

建议第一阶段采用“阻止删除被引用资源”，数据安全性更高。

### 6.3 Tool 管理

- 搜索 server 名称、工具名、description；
- 新建 MCP server；
- Flex server 卡片；
- server 详情弹窗显示工具列表；
- 编辑 `server.json` / manifest 相关配置；
- 测试连接显示耗时、返回工具数量和错误详情；
- 删除前显示引用 Agent。

资源管理页不显示“当前 Agent 是否启用”。Agent 的工具选择只在 Agent 编辑页进行。

### 6.4 知识库

分为两层：

1. 当前页面：服务状态、知识库列表、文档数量、搜索、启动 / 停止服务；
2. 知识库详情：继续复用现有知识库管理能力，后续再迁入统一组件。

Agent 与知识库的关联只在 Agent 编辑页完成。知识库页面不根据 `currentAgentName` 改变开关状态。

知识库以数据库 ID 精确关联。选中任意知识库时，保存逻辑自动保证
`knowledge_server/retrieve_knowledge` 工具可用；工具关联与知识库关联仍分别展示。

### 6.5 角色预设

角色预设的“选择”仍保留在 Chat 输入区，因为它是会话运行时操作；角色的新建、编辑、删除迁入管理中心。
角色卡片使用双列布局，名称左侧显示角色图标；编辑器作为全局弹窗渲染，不依赖当前会话模式。

输入区角色弹窗只保留：

- 搜索；
- 选择；
- 清除；
- “管理角色预设”链接。

### 6.6 快捷指令

快捷指令管理使用与其他资源一致的搜索、新建、双列卡片、悬停编辑和删除操作。编辑表单覆盖角色、预输入内容、Agent、模型、运行开关和“展示到主页”，并与主页快捷指令栏共享同一套后端数据。主页只展示已勾选的指令，使用紧凑横向卡片；主页移除仅取消展示，不删除指令。

### 6.7 常规设置

第一阶段只提供占位结构，不急于迁移语言和主题。语言、主题属于高频即时设置，继续保留顶部入口更方便。

后续可收纳：

- 默认语言和主题；
- 默认 Agent；
- 默认模型；
- 会话默认开关；
- 数据目录与诊断信息。

## 7. 组件拆分建议

当前 `ChatPage.vue` 已同时承担聊天、管理、定时任务和多个编辑弹窗。实施时必须拆分，不能只是把现有模板整体搬进一个更大的 Modal。

建议结构：

```text
web/src/
├─ components/
│  └─ management/
│     ├─ ManagementCenter.vue
│     ├─ ManagementSidebar.vue
│     ├─ ManagementPageHeader.vue
│     ├─ ResourceCard.vue
│     ├─ ResourceCardList.vue
│     ├─ AgentManagement.vue
│     ├─ AgentEditor.vue
│     ├─ SkillManagement.vue
│     ├─ SkillEditor.vue
│     ├─ ToolManagement.vue
│     ├─ ToolDetail.vue
│     ├─ KnowledgeManagement.vue
│     ├─ TimerManagement.vue
│     ├─ TimerEditor.vue
│     └─ RoleManagement.vue
├─ composables/
│  ├─ useAgentManagement.ts
│  ├─ useSkillManagement.ts
│  ├─ useToolManagement.ts
│  ├─ useKnowledgeManagement.ts
│  └─ useTimerManagement.ts
└─ pages/
   └─ ChatPage.vue
```

`ChatPage.vue` 仅保留：

- 打开 / 关闭管理中心；
- 初始菜单项；
- 定时任务跳转会话回调；
- Agent 配置变更后的列表刷新回调。

## 8. 状态设计

```ts
type ManagementSection =
  | 'timers'
  | 'agents'
  | 'skills'
  | 'tools'
  | 'knowledge'
  | 'roles'
  | 'general'

interface ManagementCenterState {
  visible: boolean
  section: ManagementSection
  search: Partial<Record<ManagementSection, string>>
}
```

规则：

- 记住用户上次打开的管理模块；
- 搜索关键字按模块保存；
- 关闭窗口不清除列表缓存；
- 重新打开时刷新当前模块；
- Agent、Skill、Tool 发生写操作后只刷新受影响模块；
- 编辑表单有未保存内容时，切换菜单或关闭窗口必须确认；
- 后端错误不得转换为空列表，应显示错误提示和重试按钮。

## 9. API 调整建议

现有 API 把“资源列表”和“当前 Agent 启用状态”混在一起，建议逐步拆开。

### 9.1 资源 API

```text
GET    /api/manage/agents
GET    /api/manage/skills
GET    /api/manage/tools
GET    /api/knowledge/databases
```

默认返回全局资源定义，不依赖当前会话或 `currentAgentName`。

### 9.2 Agent 关联 API

优先通过完整 Agent 保存：

```text
GET /api/manage/agents/{name}
PUT /api/manage/agents/{name}
```

请求中包含：

```json
{
  "skills": ["skill-a"],
  "tools": ["read_file", "calc_server/calculate"],
  "knowledge": ["db-id"],
  "subagents": ["reviewer"]
}
```

现有 Skill / Tool toggle API 可暂时保留兼容，但新 UI 不再直接调用。完成迁移后再删除。

## 10. 实施步骤

### 阶段 1：建立容器

1. 新建 `ManagementCenter.vue`；
2. 完成大窗口、左侧菜单、右侧内容区和响应式布局；
3. 左侧会话栏增加唯一“设置与管理”入口；
4. 保留旧右侧面板作为临时 fallback。

### 阶段 2：迁移管理模块

1. Agent 管理；
2. Skill 管理；
3. Tool 管理；
4. 知识库；
5. 定时任务；
6. 角色预设管理。

每迁移一个模块即从旧右侧面板移除对应模板，避免双入口长期并存。

### 阶段 3：拆分资源与关联语义

1. Skill / Tool / 知识库页面移除当前 Agent toggle；
2. Agent 编辑页增加关联分区；
3. 后端资源 API 不再依赖当前 Agent；
4. 增加引用数量和删除保护。

### 阶段 4：清理聊天界面

1. 删除 `rightPanelVisible`、`rightPanelTab`、`openRightPanel`；
2. 删除 `.right-panel` 相关模板和 CSS；
3. 删除输入框的子 Agent、Skill、Tool、知识库按钮；
4. 删除左侧导航的定时任务和 Agent 管理按钮；
5. 保留 Agent 选择器和运行时 Chat 开关；
6. 更新中日英 i18n。

### 阶段 5：清理与优化

1. 移除不再使用的状态和 computed；
2. 将管理 API 调用从 `ChatPage.vue` 移入 composables；
3. 增加 loading、error、empty、no-result 状态；
4. 补键盘导航、焦点回收和无障碍标签；
5. 更新 UI 文档和截图。

## 11. 验收标准

### 11.1 功能

- 左侧只有一个“设置与管理”入口；
- 管理窗口可切换定时任务、Agent、Skill、Tool、知识库、角色预设和快捷指令；
- 管理窗口关闭后聊天状态不丢失；
- 输入框不再出现子 Agent、Skill、Tool、知识库管理入口；
- 页面不存在右侧管理面板；
- Agent、Skill、Tool 卡片使用 Flex 自动换行；
- 每个模块都有统一搜索和追加区域；
- Agent 关联只能在 Agent 编辑页修改；
- 定时任务可新建、编辑、启停、立即执行并查看运行记录；
- 点击定时任务会话可正确关闭管理窗口并跳转；
- 角色选择仍可在 Chat 输入区完成。

### 11.2 视觉

- 桌面端至少支持 2～3 列资源卡片；
- 低于 900px 时管理窗口全屏；
- 页面头部、卡片、空态、加载和错误状态保持一致；
- 不因卡片内容差异产生严重错位；
- 长名称、长描述和大量关联项不会撑破布局。

### 11.3 数据与行为

- Skill / Tool 页面操作不隐式修改当前 Agent；
- 切换会话不改变管理中心正在编辑的目标 Agent；
- 删除被引用资源时有明确阻止或影响提示；
- 管理写入失败时保留用户输入；
- Agent 保存后新建运行实例使用最新配置；
- 已运行会话是否立即采用新配置继续遵循 Agent 运行快照策略。

### 11.4 验证

- TypeScript 类型检查；
- 前端生产构建；
- Agent / Skill / Tool / Timer CRUD 浏览器验证；
- 桌面 1440px、1024px、移动 390px 三档视觉验证；
- 键盘 Tab、Esc、Enter 和焦点回收验证；
- 中日英文案验证；
- 后端不可达、空列表、搜索无结果、保存失败等异常状态验证。

## 12. 不纳入本轮

- 修改会话与 Agent 的运行快照机制；
- 重构知识库后端；
- 原生桌面多窗口；
- 跨资源全局搜索；
- 权限系统和多用户隔离；
- 对 `model_config.json` 中敏感配置提供在线编辑。

