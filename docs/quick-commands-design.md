# 快捷指令（Quick Commands）设计

## 目标

用户在输入框下方看到一组卡片，点击即可一键切换 agent / 模型 / 聊天模式并把预设指令填入输入框（或直接发送），省去每次手动配置。

## 入口位置

聊天页输入框下方、消息列表上方，横向滚动卡片流（类似 iOS Shortcuts）。
- 第一张卡片固定为 **＋ 新建快捷指令**（虚线边框）。
- 其余卡片按创建时间倒序排列。
- 卡片过多时横向滚动，不换行。

## 卡片内容

每张卡片显示：

```
┌─────────────────────────────┐
│ 📝 标题（最多 2 行截断）      │
│ ─────────────────────────── │
│ 指令内容预览（2-3 行截断）    │
│                              │
│ [🏷️ Agent] [🤖 模型] [💬 模式] │
└─────────────────────────────┘
```

- **标题**：用户自定义，必填，最多 30 字
- **内容**：指令正文，点击后填入输入框
- **Agent**：使用哪个 agent（如 Default / Chat / Qa_worker）
- **模型**：使用哪个模型（如 bonsai2-abl / Ornith-1.5-9B）
- **聊天模式**：chat / task

## 交互

### 点击卡片

1. 切换 agent（调 `selectAgent`）
2. 切换模型（调 `selectModel`）
3. 切换聊天模式（如果与当前不同，提示需要新会话？还是直接切换？——默认直接切换，不新建会话）
4. 把指令内容填入输入框（不自动发送，用户可编辑后回车）

### 新建/编辑卡片

点击"新建"或卡片右上角编辑按钮，弹出 Modal：

```
┌─ 新建快捷指令 ────────────────┐
│ 标题: [________________]      │
│ 内容: [________________]      │
│       [多行文本框]            │
│ Agent:   [下拉选择 ▾]         │
│ 模型:   [下拉选择 ▾]         │
│ 模式:   [Chat / Task 单选]    │
│                              │
│ 自动发送? [开关]              │
│                              │
│        [取消]  [保存]         │
└──────────────────────────────┘
```

- **自动发送**开关：开启后点击卡片直接发送，不填入输入框等待编辑。
- 卡片右上角hover显示编辑/删除小按钮。

## 数据结构

存储在 `.deepagents/quick_commands.json`：

```json
[
  {
    "id": "qc_abc123",
    "title": "分析项目结构",
    "content": "分析一下当前项目存在的问题，给出改进建议",
    "agent": "default",
    "model": "bonsai2-abl",
    "mode": "task",
    "autoSend": false,
    "createdAt": 1730000000000
  }
]
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/quick-commands` | 列表 |
| POST | `/api/quick-commands` | 新建（body: 同上，不含 id/createdAt） |
| PUT | `/api/quick-commands/{id}` | 更新 |
| DELETE | `/api/quick-commands/{id}` | 删除 |

## 前端改动

- `ChatPage.vue`：输入框上方加 `QuickCommandBar` 组件
- 新建 `web/src/components/QuickCommandBar.vue`：卡片流 + 新建/编辑 Modal
- i18n：zh/en/ja 加 quickCommand 相关 key

## 改动文件

| 文件 | 改动 |
|---|---|
| `api/quick_commands.py` | 新建 CRUD 路由 |
| `app.py` | 注册路由 |
| `web/src/components/QuickCommandBar.vue` | 新建卡片组件 + Modal |
| `web/src/pages/ChatPage.vue` | 引入组件 |
| `web/src/locales/{zh,en,ja}.ts` | i18n |
