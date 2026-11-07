# 实时思考过程（Reasoning）流式展示

## 问题

推理模型（Ornith-1.5、Qwen3、DeepSeek-R1、GPT-o 系列）在正式回复前会先生成一段思考（reasoning）。
在这段时间里：
- `content` 字段为空 → 前端收不到 `agent_message_chunk`
- 画面停在"思考中"状态，没有任何内容滚动，看起来卡住了

## 现状（为什么没生效）

后端 `deepagents_acp/server.py` 已经有 `_content_updates()`：
- 它遍历 `message.content_blocks`，遇到 `type=reasoning` 的块就发 `agent_thought_chunk`
- 前端 `ChatPage.vue` 已经有 `ThoughtSegment`、`appendThoughtSegment`、渲染逻辑

**缺口**：本地 llama-server（OpenAI 兼容接口）把思考放在 `message.additional_kwargs.reasoning_content`，
而不是 `content_blocks` 里。`_visible_reasoning()` 只查 `block.get("reasoning")`，
所以本地模型的思考流被吞了，前端收不到任何 chunk。

## 方案

### 后端：monkey-patch `_content_updates`，补 `reasoning_content` 提取

在 `acp_agent.py` 启动时 patch `deepagents_acp.server._content_updates`：
- 原逻辑保留（处理 OpenAI o 系列的 encrypted reasoning block）
- 额外检查 `message.additional_kwargs.get("reasoning_content")`：
  - 非空字符串 → 包成 `AgentThoughtChunk` 发出去
  - 空/None → 不发
- 这样本地 Ornith/Qwen3 的实时思考也能流式推到前端

不直接改 site-packages：
- patch 写在我们自己的 `acp_agent.py` 里，升级包不丢
- patch 在 `import deepagents_acp` 之后、启动 server 之前执行

### 前端：thought 段改成折叠卡片

现在 thought 段和正文混排（同一段 MarkdownMessage），改成独立卡片：

```
┌─────────────────────────────────────┐
│ 💭 思考中…（已用 3.2s）    ▾/▴     │  ← 思考中：淡灰底色 + spinner
│ ┌─────────────────────────────────┐ │
│ │ 嗯，用户想做 A... 我需要先查... │ │  ← 实时流出，max-height 300px 滚动
│ └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

思考完成后折叠成一行：
```
💭 已思考 8.4s · 2,314 tokens · 点击展开
```

细节：
- 思考中：浅灰蓝底（#f0f4ff），等宽字体，右侧 spinner
- 完成：折成单行，右侧显示用时
- 点击展开/折叠
- 少于 200 字符的思考不显示（避免一闪而过的噪声）
- 正式回复（agent_message_chunk）仍走原来的 TextSegment，不混

## 改动文件

| 文件 | 改动 |
|---|---|
| `acp_agent.py` | 启动时 monkey-patch `_content_updates`，补 `reasoning_content` 提取 |
| `web/src/pages/ChatPage.vue` | thought segment 渲染改为折叠卡片组件 |
| `web/src/components/ThoughtCard.vue` | 新建：思考中/已思考两态卡片 |

## 验证

1. 本地 Ornith-1.5 模型问一个需要推理的问题（如"12345 开方是多少"）
2. 发送后立即看到思考卡片实时流出文字
3. 思考完卡片折叠成一行，正式回复开始流式
4. 切换到 GPT-5-mini：如果它返回 reasoning block，同样能看到
5. 普通短问题（无思考）：不显示卡片
