# 实时思考过程（Reasoning）流式展示

## 问题

推理模型（Bonsai-2-27B、Ornith-1.5、Qwen3、DeepSeek-R1 等）在正式回复前会先生成一段思考（reasoning）。
在这段时间里 `content` 字段为空，前端收不到 `agent_message_chunk`，画面停住像卡死。

## 两层 Patch

### 第一层：langchain-openai delta 解析

`langchain-openai` 的 `_convert_delta_to_message_chunk` 函数**不解析** `reasoning_content` 字段
（DeepSeek/ninfer/llama.cpp 的约定），即使 HTTP 响应里有这个字段，langchain 也会直接丢弃。

在 `acp_agent.py` 里包一层，把 delta 里的 `reasoning_content` 塞进 `additional_kwargs`：

```python
import langchain_openai.chat_models.base as _loc
_orig = _loc._convert_delta_to_message_chunk

def _convert_delta_with_reasoning(_dict, default_class):
    msg = _orig(_dict, default_class)
    rc = _dict.get("reasoning_content")
    if rc and hasattr(msg, "additional_kwargs"):
        msg.additional_kwargs["reasoning_content"] = rc
    return msg

_loc._convert_delta_to_message_chunk = _convert_delta_with_reasoning
```

### 第二层：`_content_updates` 转发

`deepagents_acp/server.py` 的 `_content_updates()` 原本只认 `content_blocks` 里的 `type=reasoning` 块。
patch 它，额外检查 `additional_kwargs` 里的 `reasoning_content`，包成 `AgentThoughtChunk` 发出。

两层 patch 都在 `acp_agent.py` 模块级执行，import 时即生效，不改 site-packages。

## 前端

`ThoughtCard.vue`：
- 思考中：淡蓝卡片实时流式 + 计时器（200ms 刷新）
- 完成：自动折叠成一行"已思考 · Xs · Y chars"，点击可展开
- 计时器在 done 时冻结
- reasoning 文本每 60ms 批量刷新一次（避免每个 token 重渲染 1400+ 字卡片）

## 适配的模型/服务

| 服务 | 思考字段 | 状态 |
|---|---|---|
| ninfer / llama.cpp (Bonsai-2-27B) | `delta.reasoning_content` | ✅ |
| DeepSeek API | `delta.reasoning_content` | ✅ |
| OpenAI o/GPT-5 系列 | `content_blocks` type=reasoning | ✅（原生支持） |

## 改动文件

| 文件 | 改动 |
|---|---|
| `acp_agent.py` | 两层 monkey-patch（delta 解析 + _content_updates） |
| `web/src/components/ThoughtCard.vue` | 思考卡片组件 |
| `web/src/pages/ChatPage.vue` | thought segment 渲染 + 60ms 节流 |
