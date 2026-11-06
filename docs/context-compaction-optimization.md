# 上下文压缩优化 —— 实施方案

> 状态：**已实施**（草案保留在第 1–6 节，第 7 节为实际实现与草案差异，第 8 节为验证记录）
> 适用版本：当前 main（表权威会话上下文：`RollbackAgentServer` + `utils/context_compactor.py`）
> 目标：修复"聊着聊着，上下文压缩后丢失关键信息 / 继续任务时忘记任务"的问题，同时**不影响现有主体功能**。

---

## 1. 现状与问题（已核查代码）

实际生效链路（`utils/context_compactor.py` + `api/context.py` + `acp_agent.py::RollbackAgentServer`）：

```
触发（estimate_tokens + COMPACT_OVERHEAD(6000) >= CONTENT_SIZE(32768) * 0.85）
→ split_compact：除最近 KEEP_RECENT=8 条外，其余消息全部删除
→ build_summary：把 early 消息渲染为文本；超长（> ~33423 字符）时只保留 head+tail
                 （中段用 [... omitted ...] 代替），再让模型总结（≤2048 tokens）
→ compacted = [HumanMessage(summary), *recent]
→ 写回 session-context 表；下次 prompt 用表内容重新播种 checkpoint 后执行
```

已确认的 5 个问题（按严重度）：

| # | 问题 | 后果 |
| --- | --- | --- |
| 1 | **无滚动累积**：每次压缩都对"早期全部消息（含上一次的 summary）"重新总结 | 摘要逐轮衰减（传话游戏效应），聊得越久任务细节丢得越多 —— **忘记任务的主因** |
| 2 | **任务目标消息被一刀切删除**：第一条用户消息属于 early 部分 | 原始任务描述只剩模型转述，目标细节不可靠 |
| 3 | **工具结果丢失**：读过的文件内容 / 绝对路径 / grep 结果（ToolMessage）全部删除，summary 为自由文本 | agent 失去"工作记忆"，继续任务时不知道之前在哪改、看到了什么 |
| 4 | **总结输入超长截断丢中段**：head+tail 各半，中段决策被省略 | 总结模型看不到任务执行中段的关键决策 |
| 5 | **触发偏晚 + 保留太少**：chars/1.7 低估 token；KEEP_RECENT=8 太少 | 压缩发生在窗口快爆时，留给总结的空间不足；近期上下文保留不足 |

---

## 2. 优化方案设计

### P0 —— 直接解决"忘记任务"

**① 滚动累积摘要（杜绝逐轮衰减）**
- 给 summary 消息打角色标记：`metadata={"__role__": "context_summary"}` + content 前缀 `【历史上下文摘要】`（双保险，兼容表存储丢 metadata 的情况）；
- 压缩时识别旧 summary 消息：**永不删除**，并作为总结输入**完整保留**（不参与截断）；
- `build_summary` 的 prompt 增加指令："以下是之前的对话摘要，必须完整保留其中所有要点，只对新增对话内容进行压缩"；
- 结果：每次压缩 = 旧摘要（不动） + 新段压缩 → 信息单调累积，不再衰减。

**② 任务目标锚点**
- 识别会话中**第一条用户消息**（非 summary、最早的 HumanMessage）作为任务目标；
- 压缩后**原样保留**在 summary 之后（`compacted = [summary, 任务目标, *recent]`）；
- 效果：无论压缩多少轮，模型都能看到最初的任务描述原文。

**③ 结构化总结模板**
- summary prompt 输出固定结构，强制保留关键事实：
  ```
  目标：<原始任务目标与约束>
  已完成：<步骤与结果>
  当前进度：<进行到哪一步>
  关键事实与绝对路径：<必须列出所有文件路径 / 数据 / 结论，漏列视为失败>
  已做决策：<影响后续的决策>
  待办与未解决：<未完成事项 / 正在排查的错误>
  ```
- 解决"忘了在哪干活"（路径/决策强制列全）。

### P1 —— 增强

**④ KEEP_RECENT 8 → 12**：保留更多近期上下文（最近 12 条消息原样保留）。

**⑤ 角色清晰**：summary 消息带前缀与 metadata，模型明确知道它是历史摘要而非新用户消息。

### P2 —— 稳健性

**⑥ 分块总结替代 head+tail 截断**
- 当总结输入超长时：**旧 summary 单独完整保留**；其余新内容按块（每块 ≤ 输入上限）**逐块总结后合并**，避免中段丢失；
- 实现：`build_summary` 内对超长文本按 `\n\n` 边界切块，逐块 `MODEL.ainvoke`，再合并。

**⑦ token 估算修正（可选）**
- `estimate_tokens` 保持 chars/1.7（与前端口径一致，不动，避免前端显示跳动）；
- 仅将压缩触发阈值微调（`COMPACT_RATIO` 0.85→0.8），让压缩更从容（改 env 默认值，可通过 .env 覆盖）。

---

## 3. 改动清单

| 文件 | 改动 |
| --- | --- |
| `utils/context_compactor.py` | ①⑤ 角色标记 + 滚动累积；② 任务目标锚点；③ 结构化模板；④ KEEP_RECENT=12；⑥ 分块总结；⑦ 默认阈值 0.80 |
| `api/context.py` | **不改**（调用 `compact_messages_list`，签名不变，自动获得新行为） |
| `acp_agent.py` | **不改**（`RollbackAgentServer`/`CompactingAgentServer` 均调用 compactor，签名不变） |
| `.env` | **不改**（仅改代码默认值，用户 .env 覆盖仍优先） |
| `web/src/pages/ChatPage.vue` | 仅侧栏布局调整（见第 5 节，独立于压缩改动） |

兼容性：
- `compact_messages_list(messages) -> (list, dict)` 签名不变 → api/context.py、acp_agent.py 两个调用方零改动；
- summary 消息用 HumanMessage（与原实现一致），仅增加前缀与 metadata；表存储 / checkpoint replay 均按 BaseMessage 序列化，兼容；
- 分块总结失败时回退为单块（不截断语义变化）。

---

## 4. 验证方案

1. **单元验证（压缩逻辑，不起服务）**：构造模拟消息列表（目标 + 多轮工具调用 + 长内容），调 `compact_messages_list`：
   - 断言：返回值首条为 summary（带前缀/元数据）；任务目标消息仍在；KEEP_RECENT 生效（保留最近 12 条）；token 下降；
   - 再次对压缩结果调用（模拟第二轮压缩）：断言**旧 summary 未删、内容未被重写丢失**（含前缀）；
2. **回归**：`python -c` 冒烟跑通 `should_compact` / `estimate_tokens` 边界（空列表、短列表不触发）；
3. **端到端（可选）**：重启后端 → 打开已有长会话 → 手动 `POST /api/context/compact` → 前端上下文百分比下降、消息继续正常发送。
4. **侧栏布局**：Playwright 1600x950 打开 → 构造 20+ 会话 → 断言上方区块（新建会话/定时任务/Agent 管理/搜索）完整可见、会话列表区域出现内部滚动（scrollHeight > clientHeight）且列表项完整渲染在容器内。

---

## 5. 侧栏布局调整（独立小改动）

问题：会话变多时列表把上方空间挤掉/列表溢出视口被裁。

改动（`ChatPage.vue` CSS）：
- `.chat-app`：`height:100vh` 兜底 + `height:100dvh`（防止 dvh 不支持时高度失效）；
- `.sidebar`：加 `height:100%; min-height:0`（明确高度约束）；
- `.conversation-list`：`flex:1 1 auto` → `flex:1 1 0`（basis 归零，强制吃满剩余空间并内部滚动）；
- 上方区块压缩留白，给列表更多空间：`.brand-row` padding 23px→14px、`.sidebar-nav` margin-bottom 14px→10px、`.new-conversation` margin-bottom 16px→12px、`.conversation-search` padding 8px→6px、`.conversation-label` padding 8px→6px。

---

## 6. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 压缩逻辑改动影响 prompt 流程 | 签名不变、异常兜底（`except: 继续不压缩` 已有），P0 改动集中在一个文件 |
| 任务目标锚点重复（目标消息本就在 recent 内） | 判断锚点不在 recent 才加入，避免重复 |
| summary 识别误判（用户消息恰好带前缀） | 前缀带特殊标记（`【历史上下文摘要】`），metadata 为主判据，前缀为兜底 |
| 分块总结变慢 | 仅超长才分块；每块上限可配置 |

回滚：`utils/context_compactor.py` 单文件 revert；侧栏改动单独提交可独立 revert。

---

## 7. 实际实现（与草案的差异）

草案（第 1–6 节）写于改造前。落地时对方案做了取舍，**以下为实现真相**，与草案不一致处以此节为准。

### 7.1 诊断：用户"压缩后完全不记得"的真实机械成因

| # | 缺陷 | 证据 | 修复 |
| --- | --- | --- | --- |
| 1 | **`.env` 从未生效** | `app.py` 在 `load_dotenv()` 之前就被 `api.context` 导入 → compactor 模块级常量在 import 时读 `os.getenv` 全部落回代码默认值（实测 `KEEP_RECENT=12` 而 `.env` 写 8、`RATIO=0.8` 而 `.env` 写 0.85） | compactor 顶部自行 `load_dotenv()`，并新增 `active_config()` 暴露生效值 |
| 2 | **`chars/1.7` 严重高估** | 用本地 llama-server `/tokenize` 校准：中文 1.89、英文 5.03、混合 2.98、工具 JSON 2.38、代码 3.73 chars/token。Agent 会话主体是英文/代码/JSON，一律被高估 1–2 倍 → **压缩触发过早，历史被频繁砍掉** | `estimate_text_tokens()`：CJK 1/1.85、其它 1/4.0 |
| 3 | **滚动重写导致逐轮衰减** | 旧逻辑每次把"上次摘要 + 新片段"交给小模型从头重写一份六段摘要，小模型做不到"逐字保留全部要点"，每轮都在丢条目 | **只追加的分层摘要**：长期层定稿不动，新片段只追加进近期层，超预算才合并 |
| 4 | **按消息数保留会被工具爆发吃掉** | `KEEP_RECENT=12` 条 = 一次 5 个工具调用就耗掉 10+ 条，实际保不住几轮对话 | 改为按**轮次**保留 `KEEP_TURNS`（HumanMessage 为轮边界），并受 `KEEP_TOKENS_CAP` 二次约束 |
| 5 | **压缩可能是负收益**（本次实测发现） | 只删 1 条时，186 tokens 的摘要替代 5 tokens 的原文 → 上下文反而 735→843 | `MIN_COMPACT_GAIN` 收益门限：early 部分 < 800 tokens 时不压缩（`reason="low_gain"`） |

> 补充：本地 31 条会话记录里**没有任何一条曾真正触发过压缩**（最大 10,623 tokens / 4%），所以用户看到的现象不是本地事故复现，而是上述代码隐患。

### 7.2 摘要结构（分层 + 只追加）

```
【历史上下文摘要】
## 长期记忆（早期历史，已定稿）   ← 只在 merge 时整体重写
## 近期记忆（逐步补充）           ← 每次压缩追加 "### 新增\n<六段摘要>"
```

- 六段模板：任务目标 / 已完成 / 当前进度 / 关键事实与绝对路径 / 已做决策 / 待办与未解决。
- prompt 明确告知："压缩后原文将被删除，遗漏即失败""不得用『若干文件』概括具体路径数值"。
- `parse_summary()` / `render_summary()` 双向可解析；旧格式（无标记）整段视为长期层，向前兼容。
- 预算：`SUMMARY_BUDGET_TOKENS = CONTENT_SIZE * 0.05`（262144 窗口 ≈ 13,107）；超过则 `_merge_layers()` 合并（`mode=merge`）。
- 分片：输入超过 `CONTENT_SIZE * SUMMARY_INPUT_FRACTION * 1.6` 字符时按段切分，**每片都保留完整六段**（标注 `### 片段 i`），不牺牲中段。

### 7.3 保留策略

- `split_compact()` 以 HumanMessage 为轮边界取最后 `KEEP_TURNS` 轮；
- 保留部分若超过 `KEEP_TOKENS_CAP = CONTENT_SIZE * KEEP_TOKENS_RATIO(0.5)`，逐轮收缩；
- **任务锚点（最早的用户消息）永不删除**；
- 旧 summary 消息被新 summary 完整继承，不重复保留。

### 7.4 fail-safe

`_summarize()` 返回空（异常 / 空输出 / 模型不可用）→ `compact_messages_list()` 返回 `ok=False, reason="summary_failed"` 且**原样返回原消息列表**；三个调用点（`CompactingAgentServer`、`RollbackAgentServer`、`api/context.py::_compact`）都只在 `ok=True` 时写回。绝不因摘要失败丢历史。

### 7.5 token 估算的边界处理（多模态）

真实会话里有图片消息（content 是 block 列表，含 base64）。

- `_flatten_content()` 把块列表摊平成文本（`text` block 取文本，图片块记 `[图片]`），**base64 不参与估算**；
- 图片按 `CONTEXT_IMAGE_TOKENS`（默认 750 tokens/张）单独计入 `estimate_tokens()`；
- `estimate_text_tokens()` 收到非字符串不再抛 `TypeError`（此前会直接让整条压缩链路报错）。

### 7.6 配置项（`.env`，见 `.env.bak` 模板）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CONTEXT_COMPACT_RATIO` | 0.85 | 阈值 = `CONTENT_SIZE * RATIO` |
| `CONTEXT_COMPACT_OVERHEAD` | 6000 | system prompt / 工具 schema 等不体现在消息文本里的开销 |
| `CONTEXT_COMPACT_SUMMARY_MAX_TOKENS` | 3072 | 摘要输出上限 |
| `CONTEXT_COMPACT_KEEP_TURNS` | 6 | 原样保留最近 N 轮（旧名 `CONTEXT_COMPACT_KEEP_RECENT` 仍兼容，语义已变为"轮"） |
| `CONTEXT_COMPACT_MIN_GAIN` | 800 | 收益门限：early 部分小于此 token 数则不压缩 |
| `CONTEXT_IMAGE_TOKENS` | 750 | 每张图片的估算 token |
| `CONTEXT_COMPACT_SUMMARY_INPUT_FRACTION` | 0.6 | 单次摘要输入的窗口占比上限 |

---

## 8. 调试接口

### 8.1 `GET /api/context/current`

返回**下一次 prompt 真正喂给模型**的上下文（不含 system prompt / 工具 schema，那部分由 overhead 统一估算）。

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `sessionId` | 必填 | ACP thread_id |
| `agent` | `default` | 仅用于回显 |
| `format` | `json` | `html` 渲染为浏览器可读页面 |
| `maxChars` | 500 | 每条消息预览字符数；`0` = 全文 |

JSON 字段：`config`（active_config）、`plan`（next_compaction_plan：是否触发/将删多少条多少 token/保留多少）、`summary`（long/recent/tokens）、`stats`（message_count/chars/tokens/content_size/pct）、`messages[]`（index/type/is_summary/chars/tokens/tool_calls/id/**will_drop_next_compaction**/text/truncated）。

HTML 页面：用量、消息数、压缩阈值、下次压缩、摘要用量五个卡片 + 长期/近期双栏摘要面板 + 消息表（**红底 = 下次压缩会删除，蓝底 = 摘要消息**，粘性表头，70vh 滚动）。

浏览器直接用：`http://127.0.0.1:8000/api/context/current?sessionId=<id>&format=html`
（本地无需鉴权；`&maxChars=0` 看全文）

### 8.2 `POST /api/context/compact` + `dryRun`

```json
{"sessionId": "<id>", "agent": "default", "dryRun": true}
```

- `dryRun=true`：**不写表**，返回 if 压缩后的 `before_tokens/after_tokens/pct_before/pct_after/removed/kept`、完整 `summary`（前 4000 字符）与 `after_messages[]`（type/is_summary/tokens/text），用于调 prompt；
- `dryRun` 会绕过 `MIN_COMPACT_GAIN`（`min_gain=0`），便于在小会话上强制预览。

---

## 9. 验证记录（2026-11-06）

### 9.1 单元测试（FakeModel，不调真模型）

| 断言 | 结果 |
| --- | --- |
| 按轮次切分：10 轮 + `KEEP_TURNS=2` → 保留 4 条 + 锚点，early=15 | PASS |
| 摘要失败 → `ok=False, reason=summary_failed` 且历史原样 | PASS |
| 首次压缩 `mode=append`、含分层头、保留关键事实（`out_7.json`） | PASS |
| token 下降 | PASS（1335 → 1032 的真实模型实测） |
| 第二次压缩：长期层**逐字不变**、近期层追加 | PASS |
| 旧摘要不重复、只有一个 summary 消息 | PASS |
| 超预算 → `mode=merge`、近期层清空、长期层保留合并且事实不丢 | PASS |
| 收益门限：early 太小 → `low_gain` 且不动历史；`min_gain=0` 可强制 | PASS |
| 英文估算 ≈ len/4（校准后不再 2 倍高估） | PASS |
| `should_compact` 真/假两向 | PASS |

### 9.2 真实模型（本地 llama-server / Ornith-1.5-9B，小窗口 CONTENT_SIZE=2400、RATIO=0.5、KEEP_TURNS=3）

- 50 条消息 / 1335 tokens → 压缩后 1032 tokens，removed 41 条，kept 9 条（含锚点）；
- 摘要六段结构完整，保留 `out_7.json`、`412 行`、`88 行`、`0.80`、`131072`、`CONTEXT_COMPACT_RATIO` 等路径与数值。

### 9.3 接口（ASGI TestClient 直连，含图片会话 `dc48b25947894115bd0356776c129735`）

- JSON：`ok=True`、`config` 反映 `.env`（`keep_turns=6`、`summary_max_tokens=3072`）、`plan.threshold=222822`；
- 多模态消息被摊平，`data:image` 不出现、`[图片]` 占位、`truncated=false`、图片计入 token（930）；
- HTML：`content-type: text/html; charset=utf-8`，含"当前上下文 / 分层摘要 / 消息列表"，浏览器渲染正常（已截图确认）；
- 未知会话 → `no_context_record` + 中文提示；
- `maxChars=0` → 不截断；
- `dryRun=true`：表内容 md5 前后一致（**未写库**）、返回预览摘要与 `after_messages`。

### 9.4 附带修复

- `app.py` shutdown 路径 `close_mcp_clients()` 未 `await`（`RuntimeWarning: coroutine was never awaited`）→ 改为 `await`。

### 9.5 已知限制

- 分片摘要时各片都带完整六段，片段间会有重叠表述；真实窗口（262144）下几乎不会分片，暂不做片段归并。
- `dryRun` 与真实压缩都直接调线上模型端点，一次压缩约 10–20s（本地 9B）；网关已放在线程池（`asyncio.to_thread`）里跑，不阻塞事件循环。
- 摘要质量由小模型决定，`summary_failed` 时宁可不压缩（这是设计选择，不是缺陷）。
