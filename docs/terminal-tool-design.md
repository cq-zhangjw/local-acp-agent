# Terminal 工具（结果直通）设计方案

> 状态：**已实施并测试通过**（2026-11-01）。改动：`utils/terminal_tools.py`（新增）+ `utils/model_util.py`（TerminalModel）。
> 验证：冒烟 8 用例、完整 agent 图集成测试（模型仅 1 次调用 + calc 真实执行）、回归 3 场景（非白名单/多工具并行/Chat 零影响）、ACP 事件层确认。
> 目标：对"结果即最终答案"的工具（时间/计算/注音等），跳过"模型复述"这一轮请求，减少交互与 token 消耗。

## 一、机制回顾

```
普通工具（现状）：
  模型请求 → tool_call → 执行工具 → ToolMessage 进历史 → 模型再次请求（重发全部历史）→ 模型复述结果

Terminal 工具（改造后）：
  模型请求 → tool_call → 执行工具 → 结果直接作为最终回复推前端（不再次请求模型）
```

生效条件（两条都满足才直通）：
1. 该工具在 terminal 白名单中（静态声明）
2. **本轮只有一个工具调用**（模型并行调了多个工具 → 走正常流程，防止丢其它工具结果）

## 二、现状链路与落点分析（探查结论）

- 项目 agent 基于 langgraph 循环图：**单次 `agent.astream()` 内部**完成 model→tools→model→…→END（`deepagents_acp/server.py` 约 1008 行 `async for stream_chunk in agent.astream(...)`）；外层 `while current_state is None or current_state.interrupts` 只处理 HITL 中断，与工具循环无关。
- 因此"工具执行后、模型复述前"的切断点**在图内部**，不在 deepagents_acp 的流式循环里（在流式循环里 break 无法阻止 langgraph 继续执行模型节点，token 照样消耗）。

**落点对比：**

| 方案 | 位置 | 结论 |
|---|---|---|
| middleware `before_model` | langchain agents middleware | ❌ 该钩子只能改 state，**不能短路模型调用**（签名返回 `dict|None` 仅作 state update） |
| 改 deepagents_acp 流式循环 | `.venv` 库 | ❌ 无法阻止图内第二轮模型请求；且改库会被升级覆盖 |
| 修改 langgraph 图结构（tools→END 边） | deepagents 主库内部 | ❌ 侵入库内部 |
| **模型包装短路（推荐）** | 项目自有代码 | ✅ 不动任何库：包装 `create_model()` 返回的模型对象，覆写 `astream`/`ainvoke`，检测到 terminal 场景时直接返回格式化结果，不真正调模型 |

## 三、改动清单（全部项目自有文件）

### 1. 新增 `utils/terminal_tools.py` —— terminal 白名单

```python
"""Terminal tool registry: 工具结果即最终答案，模型不再复述。"""

# key 为 MCP 工具加载后的全名（见 mcp_loader.make_tool: mcp_<server>_<tool>）
# 或内置工具名（execute/read_file 等，不建议）
TERMINAL_TOOLS: dict[str, dict] = {
    "mcp_calc_server_calculate": {"format": "结果：{result}"},
    # 后续扩展：时间工具、注音工具等
}


def is_terminal(tool_name: str) -> bool:
    return tool_name in TERMINAL_TOOLS


def format_terminal_result(tool_name: str, result: str) -> str:
    """按工具配置的模板格式化结果（默认原样返回）。"""
    fmt = TERMINAL_TOOLS.get(tool_name, {}).get("format")
    if not fmt:
        return result
    try:
        return fmt.format(result=result)
    except Exception:
        return result
```

### 2. 修改 `utils/model_util.py` —— 模型包装短路

```python
# 追加（导入放在文件顶部，避免循环依赖）：
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from utils.terminal_tools import format_terminal_result, is_terminal


class TerminalModel:
    """包装 ChatOpenAI：上一轮刚执行了 terminal 工具时短路返回结果，不真正调模型。

    duck-type 兼容 BaseChatModel 的 astream/ainvoke 两个入口，
    deepagents 主库与 deepagents_acp 均零改动。
    """

    def __init__(self, inner):
        self._inner = inner
        # 透传 ChatOpenAI 常用属性（model_name 等被 deepagents/前端读取）
        self.model_name = getattr(inner, "model_name", "")

    def _short_circuit_text(self, messages) -> str | None:
        """满足 terminal 条件返回格式化结果文本，否则 None。

        messages: deepagents model 节点传入的历史消息列表。
        判断：最后一条是 ToolMessage；往前找最近的带 tool_calls 的 AIMessage；
        若其 tool_calls 恰好 1 个且工具名在白名单 → 直通。
        """
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, ToolMessage):
            return None
        for m in reversed(messages[:-1]):
            if isinstance(m, AIMessage) and m.tool_calls:
                if len(m.tool_calls) != 1:
                    return None  # 本轮多工具并行 → 不直通
                name = m.tool_calls[0]["name"]
                if not is_terminal(name):
                    return None
                return format_terminal_result(name, str(last.content))
        return None

    async def astream(self, messages, stop=None, **kwargs):
        text = self._short_circuit_text(messages)
        if text is not None:
            yield AIMessageChunk(content=text)  # 实施时按 ChatOpenAI.astream 的产出类型核对
            return
        async for chunk in self._inner.astream(messages, stop=stop, **kwargs):
            yield chunk

    async def ainvoke(self, messages, **kwargs):
        text = self._short_circuit_text(messages)
        if text is not None:
            return AIMessage(content=text)
        return await self._inner.ainvoke(messages, **kwargs)


def create_model(model_name: str | None = None):
    """（原逻辑不变，返回前包一层）"""
    # ... 现有 kwargs 组装 ...
    raw = init_chat_model(**kwargs)
    return TerminalModel(raw)
```

> 实施注意：`astream` 短路的产出类型需与 `ChatOpenAI.astream` 的实际产出（`ChatGenerationChunk` 或 `AIMessageChunk`）保持一致，实施时对照 `.venv\Lib\site-packages\langchain_openai\chat_models\base.py` 的 `_stream` 行为核对一次；若为 `ChatGenerationChunk`，改为 `yield ChatGenerationChunk(message=AIMessageChunk(content=text))`。

### 3. `acp_agent.py` —— 无需改动

`build_agent` 已经统一走 `create_model()`（Chat 分支与 agent 分支都是），包装对两分支同时生效：
- **Chat 分支**：`tools=[]`，白名单里的 MCP 工具不会出现在 Chat，短路永不触发 → 零影响
- **agent 分支**：只有白名单工具被调用且单工具时才短路

## 四、calc_server 改造步骤（用户点名示例）

**当前 calc_server 文件：**
```
.deepagents/tools/mcp_servers/calc_server/
├── calc_mcp_server.py   # fastmcp，calculate(expression)
└── manifest.json        # 工具元数据，加载后工具全名为 mcp_calc_server_calculate
```

**步骤（共 3 步）：**

1. **加白名单**：在 `utils/terminal_tools.py` 的 `TERMINAL_TOOLS` 加一行：
   ```python
   "mcp_calc_server_calculate": {"format": "结果：{result}"},
   ```
2. **无需改 calc_mcp_server.py / manifest.json**（白名单制，工具本身不感知）。
3. **重启后端**（`app.py`），验证：

   ```
   用户："2 + 3 * 4 等于几？"
   现状：工具卡片显示 14 + 模型复述"根据计算，2+3*4=14..."
   改造后：工具卡片显示 14 + 最终回复直接是"结果：14"（无复述请求，省一整轮历史重发）
   ```

**可选扩展（声明制）**：如果希望"工具自己声明 terminal"而不是集中白名单，改为在 `manifest.json` 的 tool 条目加 `"_meta": {"terminal": true}`，并在 `utils/mcp_loader.py` 的 `make_tool()` 里读取该标记注册进 terminal 表。两种方式等价，白名单制改动更小，推荐先用白名单。

## 五、对现有功能的影响

- **无破坏性**：白名单为空时行为完全不变；非白名单工具、多工具并行、多轮串行全部走原流程
- 前端**零改动**：工具卡片照常显示（`update_tool_call completed`），短路后的结果作为正常 AI 消息推前端
- checkpoint 一致性：短路产生的 AIMessage（含结果文本）照常入库，下次对话模型能看到"刚才的结果"，语义连贯
- agent 模式安全：`execute`/`read_file` 等不加白名单，复杂任务不受影响

## 六、验证方式

1. **冒烟**：包装模型单测——构造 `[AIMessage(tool_calls=[calc]), ToolMessage("14")]` → `ainvoke` 返回 `AIMessage(content="结果：14")`，且 inner 模型未被调用（mock 断言）
2. **链路**：真实提问"2+3*4"，前端出现工具卡片 + "结果：14"，日志中无第二次模型请求（观察 8088 请求计数或 WS 事件）
3. **回归**：多工具任务（"算 1+1 并读一下 xxx 文件"）仍走模型综合；非白名单工具（execute）行为不变

## 七、风险与防护

| 风险 | 防护 |
|---|---|
| 模型并行调用多工具（含 terminal）丢结果 | 单工具条件（`len(tool_calls) != 1` 不短路） |
| 工具结果太长直接当回复不友好 | 白名单带 `format` 模板，按工具定制展示 |
| 包装类类型兼容 | 继承/duck-type 保持一致；实施时核对 astream 产出类型 |
| terminal 工具被 agent 模式误用 | 白名单按场景评估后手动加入，默认不收录 execute 类 |
