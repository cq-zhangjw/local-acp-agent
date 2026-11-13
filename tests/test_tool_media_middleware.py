"""Verification for utils/tool_media_middleware.py (no LLM call).

Run:  .venv\\Scripts\\python.exe tests\\test_tool_media_middleware.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_openai.chat_models.base import _convert_message_to_dict  # noqa: E402

from utils.tool_media_middleware import transform_messages  # noqa: E402


def img_tool(call_id: str, path: str) -> ToolMessage:
    return ToolMessage(
        content_blocks=[{"type": "image", "base64": "AAAA", "mime_type": "image/png"}],
        tool_call_id=call_id,
        name="read_file",
        additional_kwargs={"read_file_path": path},
    )


def ai_calls(*ids: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "read_file", "args": {}, "id": i} for i in ids])


def main() -> None:
    # Parallel tool calls: human image message must come after BOTH tool messages.
    msgs = [HumanMessage("see"), ai_calls("a", "b"), img_tool("a", "x.png"), ToolMessage(content="text", tool_call_id="b", name="ls")]
    snapshot = repr(msgs)
    out = transform_messages(msgs)
    assert repr(msgs) == snapshot, "input mutated"
    assert [type(m).__name__ for m in out] == ["HumanMessage", "AIMessage", "ToolMessage", "ToolMessage", "HumanMessage"], out
    assert all(b.get("type") != "image" for b in out[2].content), out[2].content
    assert any(b.get("type") == "image" for b in out[-1].content)

    # OpenAI payload: tool messages are text-only, image sits in the user message.
    d = [_convert_message_to_dict(m) for m in out]
    assert d[2]["role"] == "tool" and all(b["type"] == "text" for b in d[2]["content"])
    assert d[-1]["role"] == "user" and d[-1]["content"][1]["type"] == "image_url"

    # Only the newest image is re-sent by default.
    msgs = [ai_calls("a"), img_tool("a", "old.png"), ai_calls("b"), img_tool("b", "new.png")]
    out = transform_messages(msgs)
    humans = [m for m in out if isinstance(m, HumanMessage)]
    assert len(humans) == 1 and out[-1] is humans[0]
    assert "omitted" in out[1].content[0]["text"]

    # No images: same list object returned.
    plain = [HumanMessage("hi")]
    assert transform_messages(plain) is plain
    print("all passed")


if __name__ == "__main__":
    main()
