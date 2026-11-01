import json
import os
from pathlib import Path

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.messages.utils import convert_to_messages
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

from utils.terminal_tools import format_terminal_result, is_terminal

MODEL_CONFIG_PATH = Path(__file__).resolve().parent.parent / "model_config.json"


def _load_configs() -> list[dict]:
    try:
        with open(MODEL_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def current_model_name() -> str:
    """模型名由前端选择并经 WS 查询参数注入为环境变量 MODEL_NAME（参考 AGENT_NAME 的传递）。

    未注入时回退到 model_config.json 的第一个模型。
    """
    name = os.getenv("MODEL_NAME", "").strip()
    if name:
        return name
    configs = _load_configs()
    return configs[0].get("model_name") if configs else ""


def current_content_size() -> int:
    """当前模型的上下文窗口大小（tokens），前端上下文比例总数与压缩阈值都以它为准。"""
    cfg = get_model_config(current_model_name(), raise_on_missing=False)
    try:
        return int((cfg or {}).get("content_size") or 0)
    except (TypeError, ValueError):
        return 0


def list_models() -> list[dict]:
    return _load_configs()


def get_model_config(model_name: str, raise_on_missing: bool = True) -> dict:
    configs = _load_configs()
    for cfg in configs:
        if cfg.get("model_name") == model_name:
            return cfg
    if raise_on_missing:
        available = ", ".join(c.get("model_name", "?") for c in configs)
        raise ValueError(
            f"MODEL_NAME '{model_name}' not found in {MODEL_CONFIG_PATH.name}. Available: {available}"
        )
    return {}


class TerminalModel(ChatOpenAI):
    """ChatOpenAI 子类：上一轮刚执行了 terminal 工具时，短路返回工具结果，不再请求模型。

    通过继承保留 BaseChatModel 的全部能力（bind_tools / with_config / stream 等，
    deepagents 主库与 deepagents_acp 如何调用都兼容），只覆写两个标准异步入口
    astream / ainvoke：命中 terminal 场景时在真正调用模型之前返回，省一整轮
    模型请求（历史重发 token + 复述输出 token）。
    """

    def _short_circuit_text(self, messages) -> str | None:
        """满足 terminal 条件返回格式化结果文本，否则返回 None。

        判断：最后一条消息是 ToolMessage；往前找最近的带 tool_calls 的 AIMessage；
        若其 tool_calls 恰好 1 个且工具名在白名单 -> 直通（结果即最终答案）。
        """
        if not messages:
            return None
        try:
            msgs = messages
            if isinstance(msgs, list) and msgs and not isinstance(msgs[0], BaseMessage):
                msgs = convert_to_messages(msgs)
        except Exception:
            msgs = messages
        if not msgs:
            return None
        last = msgs[-1]
        if not isinstance(last, ToolMessage):
            return None
        for m in reversed(msgs[:-1]):
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                if len(m.tool_calls) != 1:
                    # 本轮并行调了多个工具 -> 需要模型综合，不直通
                    return None
                name = m.tool_calls[0].get("name", "")
                if not is_terminal(name):
                    return None
                return format_terminal_result(name, str(last.content))
        return None

    async def astream(self, messages, config=None, stop=None, **kwargs):
        text = self._short_circuit_text(messages)
        if text is not None:
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))
            return
        async for chunk in super().astream(messages, config=config, stop=stop, **kwargs):
            yield chunk

    async def ainvoke(self, messages, config=None, stop=None, **kwargs):
        text = self._short_circuit_text(messages)
        if text is not None:
            return AIMessage(content=text)
        return await super().ainvoke(messages, config=config, stop=stop, **kwargs)


def create_model(model_name: str | None = None):
    """Build a chat model; reloads model_config.json on every call.

    The model name follows the frontend selection (WS query param -> env
    MODEL_NAME), same pattern as AGENT_NAME.
    """
    load_dotenv(override=True)

    model_name = model_name or current_model_name()
    timeout = float(os.getenv("TIMEOUT")) if os.getenv("TIMEOUT") else None
    max_retry = int(os.getenv("MAX_RETRY")) if os.getenv("MAX_RETRY") else 3

    cfg = get_model_config(model_name)
    top_p = cfg.get("top_p")

    kwargs = dict(
        model=model_name,
        base_url=cfg.get("endpoint") or "http://127.0.0.1:8088",
        api_key=cfg.get("api_key") or os.getenv("API_KEY") or None,
        timeout=timeout,
        max_retries=max_retry,
        temperature=float(cfg["temperature"]) if cfg.get("temperature") is not None else 0.5,
        max_tokens=int(cfg["max_tokens"]) if cfg.get("max_tokens") is not None else 65535,
    )
    if top_p is not None:
        kwargs["top_p"] = float(top_p)

    # TerminalModel 继承 ChatOpenAI：命中 terminal 工具时短路返回结果，
    # 其余情况行为与普通 ChatOpenAI 完全一致（白名单为空时零影响）。
    # 仅对 openai provider 启用 terminal 能力；其它 provider 走原路径不变。
    provider = cfg.get("model_provider") or "openai"
    if provider == "openai":
        return TerminalModel(**kwargs)
    kwargs["model_provider"] = provider
    return init_chat_model(**kwargs)
