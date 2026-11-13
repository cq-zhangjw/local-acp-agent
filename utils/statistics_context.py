from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from threading import Lock

from langchain_core.callbacks import AsyncCallbackHandler


@dataclass
class TurnMetrics:
    record_id: str
    ai_call_count: int = 0
    tool_call_count: int = 0

    def __post_init__(self) -> None:
        self._lock = Lock()

    def increment_ai(self) -> None:
        with self._lock:
            self.ai_call_count += 1

    def increment_tool(self) -> None:
        with self._lock:
            self.tool_call_count += 1


_CURRENT_METRICS: ContextVar[TurnMetrics | None] = ContextVar(
    "statistics_turn_metrics", default=None
)


def activate_metrics(metrics: TurnMetrics) -> Token[TurnMetrics | None]:
    return _CURRENT_METRICS.set(metrics)


def reset_metrics(token: Token[TurnMetrics | None]) -> None:
    _CURRENT_METRICS.reset(token)


class StatisticsCallbackHandler(AsyncCallbackHandler):
    async def on_chat_model_start(self, *args, **kwargs) -> None:
        metrics = _CURRENT_METRICS.get()
        if metrics is not None:
            metrics.increment_ai()

    async def on_tool_start(self, *args, **kwargs) -> None:
        metrics = _CURRENT_METRICS.get()
        if metrics is not None:
            metrics.increment_tool()


statistics_callback_handler = StatisticsCallbackHandler()
