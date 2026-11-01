# Chat 历史记录开关（history_on）设计方案

> 状态：**已实施并测试通过**（2026-11-01）
> 需求：纯聊天场景（Chat / 小A）可关闭历史上下文 —— 关 = 每次提问只带当前这一条消息（无状态问答，省 token）；开 = 携带完整历史（现状）。开关可随时切换，会话历史照常保存与回放显示。

---

## 一、目标与边界

| 项 | 说明 |
|---|---|
| 开关语义 | **history_on=true**：prompt 携带完整会话历史（现状，默认）<br>**history_on=false**：prompt 只携带当前这一条用户消息（模型输入 = 系统提示词 + 当前轮） |
| 影响范围 | 仅**模型输入**。历史表 `db/db_session_content_<hash8>` 照常累积，会话回放/前端显示、压缩、cancel/retry 全部不受影响 |
| 生效场景 | Chat 模式（小A）工具栏提供开关；后端对 **agent 模式同样生效**（同一 server），agent 模式前端不显示开关、恒传 `history_on: true` |
| 默认值 | `true`（老客户端不传 / 非 Chat 模式 = 现状行为，零回归风险） |

**为什么在重播种层做而不是截断 langgraph 消息**：项目历史权威源是 `utils/session_context.py` 的表（`RollbackAgentServer` 每次 prompt 都从表重播种 checkpoint）。在重播种处控制"播种什么"是唯一干净的位置——表与 checkpoint 快照照常，只是模型输入里没有旧历史。

---

## 二、状态流转（前端 → 后端）

```
ChatPage.vue 工具栏 toggle（仅 chat 模式显示）
  → localStorage 持久化（key: chat_history_on，默认 true）
  → 发消息时携带 _meta.history_on（ACP prompt 的 _meta 字段）
  → acp router 自动解包 _meta → kwargs（现有 retry 机制同通道，已验证）
  → RollbackAgentServer.prompt 读取 kwargs.get("history_on", True)
```

**通道已确认**（探查结论）：
- 前端 `callAcp('session/prompt', { sessionId, prompt, _meta: { retry, ... } })`
- `.venv/Lib/site-packages/acp/router.py` `_make_func`：`params.update(meta)` → `prompt(**params)`，`_meta` 全部进入 kwargs
- `RollbackAgentServer.prompt` 已有 `kwargs.get("retry")` 先例 → `history_on` 走完全相同的通道

---

## 三、后端改动（唯一落点：`acp_agent.py` `RollbackAgentServer.prompt` 步骤 4 重播种）

现状（约 516-524 行）：

```python
# ---- 4) re-seed the checkpoint from the table ----
try:
    agent = self._checkpointed_agent(session_id)
    config = self._session_config(session_id)
    objs = convert_to_messages(context_messages) if context_messages else []
    await agent.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *objs]},
        as_node="__start__",
    )
except Exception: ...
```

改为（在 prompt 开头读开关，重播种时只播种空列表）：

```python
# history_on: 关闭时本次 prompt 只带当前这一条消息（无状态问答）。
# 当前用户消息由 AgentServerACP.prompt 在重播种后追加，因此这里播种空
# 列表即可让模型只看到本轮；表/checkpoint 快照照常累积，不影响回放。
history_on = kwargs.get("history_on", True)

# ---- 4) re-seed the checkpoint from the table ----
try:
    agent = self._checkpointed_agent(session_id)
    config = self._session_config(session_id)
    objs = convert_to_messages(context_messages) if context_messages else []
    if not history_on:
        objs = []
    await agent.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *objs]},
        as_node="__start__",
    )
except Exception: ...
```

**需要同步改的（实施中发现的关键点）**：
- 步骤 5 回写表：关历史时 checkpoint 被清空重播，本轮执行后 checkpoint 只剩当前轮；直接回写会**覆盖表里累积的历史**。必须合并旧历史 + 本轮再回写：

```python
if not history_on:
    pre_objs = convert_to_messages(self._pre_prompt_messages.get(session_id) or [])
    final_messages = [*pre_objs, *final_messages]
sc_save_context(session_id, final_messages)
```

**不需要改的**（保持现状）：
- 步骤 1 读表 / 首次播种 —— 照旧（表始终是全历史）
- 步骤 2 压缩 —— 照旧（作用于表内容）
- 步骤 3 快照（cancel 基线）—— 照旧
- cancel 分支、`_rollback_checkpoint` —— 照旧（cancel 时恢复 pre-prompt 快照，本轮废弃）
- retry 分支 —— 照旧

**关键边界**：`history_on=false` 时，`AgentServerACP.prompt` 的流式循环会把当前 prompt 作为唯一的 user message 追加进 checkpoint（重播种空 + 追加本轮 = 模型只见本轮）。已验证 deepagents_acp 的 prompt 流：`{"messages": [{"role": "user", "content": content_blocks}]}`。

---

## 四、前端改动（`web/src/pages/ChatPage.vue`）

### 1. 状态与持久化（对齐现有 `MODEL_STORAGE_KEY = 'chat_model'` 模式）

```ts
const HISTORY_STORAGE_KEY = 'chat_history_on'
const historyOn = ref<boolean>(localStorage.getItem(HISTORY_STORAGE_KEY) !== 'false') // 默认 true
```

### 2. 工具栏 UI（`composer-toolbar-left` 的 chat 分支，约 3748 行附近）

现有 chat 分支：

```html
<template v-else>
  <NButton quaternary size="small" class="toolbar-pill agent-pill" disabled>
    <template #icon><NIcon :component="SparklesOutline" /></template>{{ actionLabels.chatMode }}
  </NButton>
</template>
```

在其后追加历史开关（复用 `toolbar-pill` 样式，开关状态高亮）：

```html
<NButton
  quaternary size="small"
  class="toolbar-pill"
  :class="{ 'toolbar-pill--on': historyOn }"
  :aria-label="actionLabels.historyToggle"
  @click="historyOn = !historyOn; localStorage.setItem(HISTORY_STORAGE_KEY, String(historyOn))"
>
  <template #icon><NIcon :component="historyOn ? HistoryOutline : TimeOutline" /></template>
  {{ historyOn ? actionLabels.historyOn : actionLabels.historyOff }}
</NButton>
```

（图标：naive-ui 的 `HistoryOutline` / `TimeOutline`；`toolbar-pill--on` 新增 CSS 高亮态，样式与 `.model-picker-item--active` 视觉一致）

### 3. 发送时携带（`sendAgentPrompt` 约 2490 行）

现状：

```ts
await callAcp('session/prompt', { sessionId: conversation.agentSessionId ?? conversation.id, prompt: content, _meta: retry ? { retry: true } : {} })
```

改为：

```ts
const meta: Record<string, unknown> = { history_on: historyOn.value }
if (retry) meta.retry = true
await callAcp('session/prompt', { sessionId: conversation.agentSessionId ?? conversation.id, prompt: content, _meta: meta })
```

（`history_on` 全局偏好随每条消息携带；切会话/切模式不影响。重发/重试同样携带当前开关值。）

---

## 五、测试方案

| 用例 | 预期 | 结果 |
|---|---|---|
| 集成（真实 ACP stdio + Chat 分支 + 假模型）：首轮默认 | 模型收到 system + 本轮（2 条） | ✅ |
| 集成：history_on=false 第二轮 | 模型只见当前轮，不含前轮"张三" | ✅ 2 条、无张三 |
| 集成：history_on=true 第三轮 | 模型收到完整历史（含"张三"） | ✅ 6 条、含张三（表累积未丢） |
| 前端构建 | `npm run build` 通过 | ✅ built in 17.47s |
| 真实链路（8088 在线时）：Chat 模式问 A 再问 B（关历史）→ B 的回复不引用 A | 需 8088 在线，标记为待验证 | ⏳ |

---

## 六、风险与回退

- 老客户端（不传 `_meta.history_on`）→ 默认 true → 零影响
- agent 模式前端不传 `history_on: false` → 默认 true → 零影响
- 回退：删除前端 toggle + 后端一处 `if not history_on: objs = []`，即恢复现状
