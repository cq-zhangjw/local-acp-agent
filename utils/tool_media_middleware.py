"""Move images out of ToolMessages into a following HumanMessage for the model call.

OpenAI Chat Completions only accepts text in `role: "tool"` messages, so an image
returned by `read_file` never reaches the model. Per request, this middleware
replaces each tool-result image with a text placeholder and re-sends the image in
a user message placed after the whole tool-result group. Stored state is untouched.

Only the newest TOOL_IMAGE_KEEP_LAST images (default 1) are re-sent; older ones
keep just the placeholder so base64 is not replayed on every turn.
"""

from __future__ import annotations

import os
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage


def _keep_last() -> int:
    try:
        return max(0, int(os.getenv("TOOL_IMAGE_KEEP_LAST", "1")))
    except ValueError:
        return 1


def _is_image(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "image" and bool(block.get("base64"))


def _count_images(msg: Any) -> int:
    if not isinstance(msg, ToolMessage) or not isinstance(msg.content, list):
        return 0
    return sum(1 for b in msg.content if _is_image(b))


def transform_messages(messages: list[Any]) -> list[Any]:
    """Return messages with tool-result images relocated; the input list is not mutated."""
    if not any(_count_images(m) for m in messages):
        return messages

    # Newest-first budget: how many images of each message are re-sent.
    budget = _keep_last()
    allowed: dict[int, int] = {}
    for i in range(len(messages) - 1, -1, -1):
        n = _count_images(messages[i])
        if n:
            allowed[i] = min(n, budget)
            budget -= allowed[i]

    out: list[Any] = []
    pending: list[dict] = []

    def flush() -> None:
        if pending:
            out.append(HumanMessage(content=[{"type": "text", "text": "The image(s) returned by the tool call(s) above:"}, *pending]))
            pending.clear()

    for i, msg in enumerate(messages):
        if not isinstance(msg, ToolMessage):
            flush()
            out.append(msg)
            continue
        if not _count_images(msg):
            out.append(msg)
            continue

        path = (msg.additional_kwargs or {}).get("read_file_path", "file")
        sent = 0
        new_blocks: list[Any] = []
        for block in msg.content:
            if not _is_image(block):
                new_blocks.append(block)
            elif sent < allowed.get(i, 0):
                sent += 1
                pending.append({"type": "image", "base64": block["base64"], "mime_type": block.get("mime_type", "image/png")})
                new_blocks.append({"type": "text", "text": f"[image {path} is attached in the next user message]"})
            else:
                new_blocks.append({"type": "text", "text": f"[image {path} omitted from history]"})
        out.append(msg.model_copy(update={"content": new_blocks}))

    flush()
    return out


class ToolMediaToHumanMiddleware(AgentMiddleware):
    """See module docstring."""

    def _fix(self, request):
        original = list(request.messages)
        messages = transform_messages(original)
        return request if messages is original else request.override(messages=messages)

    def wrap_model_call(self, request, handler):
        return handler(self._fix(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._fix(request))
