"""Context compaction for long ACP sessions.

When the model-input tokens of a session approach the model context window,
older turns are summarized and removed from the conversation history so the
session can continue without hitting the model's hard context limit.

Design (redesigned for information retention):

- **Append-only layered summary.** The stored summary is split into
  "长期记忆" (long-term, frozen) and "近期记忆" (recent additions). A new
  compaction only *appends* a freshly summarized block to 近期记忆; it never
  rewrites the existing text. Small local models degrade badly when asked to
  rewrite a long summary every round, which used to cause round-by-round
  information decay. A merge (the only rewrite) happens only when the summary
  exceeds its token budget.
- **Turn-based retention.** The last N *turns* (a turn starts at a user
  message) are kept verbatim instead of the last N messages, so tool-call
  bursts cannot eat the keep-window. A token cap shrinks the window when a
  single turn is enormous.
- **Task-goal anchor.** The earliest user message survives compaction verbatim.
- **Fail-safe.** If summarization fails (empty output / exception) the history
  is NOT compacted; nothing is dropped on a bad summary.
- **Tokenizer-accurate estimate.** CJK and other characters are weighted
  separately (measured against the local llama-server tokenizer: ~1.85 chars
  per token for Chinese, ~4.0 for English/code/JSON). The old `chars/1.7`
  overestimated English/tool output by 100-200%, which triggered compaction far
  too early.

Entrypoints:
- compact_messages_list: pure function used by the table-authoritative flow and
  by POST /api/context/compact;
- compact_messages: legacy checkpoint-based compaction.
"""

import os
import re

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage, RemoveMessage

from utils.model_util import create_model, current_content_size

# Load .env at import time. Modules that import this one may run before app.py's
# own load_dotenv() call (e.g. api.context), which used to leave every
# CONTEXT_COMPACT_* setting at its code default.
load_dotenv()

# Context window size (tokens), same as the frontend display value.
# Source: model_config.json -> the model selected by the frontend (WS query
# param -> env MODEL_NAME, same pattern as AGENT_NAME).
CONTENT_SIZE = current_content_size() or 32768
# Compaction triggers when estimated tokens >= CONTENT_SIZE * RATIO.
COMPACT_RATIO = float(os.getenv("CONTEXT_COMPACT_RATIO") or 0.80)
# Extra tokens not counted by the message estimator (system prompt, tool
# schemas, images, ...). Keeps the check conservative.
COMPACT_OVERHEAD = int(os.getenv("CONTEXT_COMPACT_OVERHEAD") or 6000)
# Per-image token estimate (image blocks carry no text but are billed as vision
# tokens); only used for display/threshold purposes.
IMAGE_TOKEN_ESTIMATE = int(os.getenv("CONTEXT_IMAGE_TOKENS") or 750)
# Do not compact when the early part is smaller than this many tokens: the
# summary would cost more than it saves (and would keep growing every round).
MIN_COMPACT_GAIN = int(os.getenv("CONTEXT_COMPACT_MIN_GAIN") or 800)
# Keep the most recent N turns (a turn starts at each user message). The legacy
# CONTEXT_COMPACT_KEEP_RECENT name is accepted and now means turns as well.
KEEP_TURNS = int(
    os.getenv("CONTEXT_COMPACT_KEEP_TURNS")
    or os.getenv("CONTEXT_COMPACT_KEEP_RECENT")
    or 6
)
# Hard cap for the kept verbatim part (fraction of the window): one giant turn
# (a long tool dump) must not swallow the whole budget.
KEEP_TOKENS_RATIO = float(os.getenv("CONTEXT_COMPACT_KEEP_TOKENS_RATIO") or 0.5)
# Maximum tokens the summarizer may produce per call.
SUMMARY_MAX_TOKENS = int(os.getenv("CONTEXT_COMPACT_SUMMARY_MAX_TOKENS") or 3072)
# Total budget for the stored summary; above it the two layers are merged.
SUMMARY_BUDGET_RATIO = float(os.getenv("CONTEXT_COMPACT_SUMMARY_BUDGET_RATIO") or 0.05)
# Summarizer input guard: at most ~this fraction of the window is sent to the
# model per summarizing call.
SUMMARY_INPUT_FRACTION = float(os.getenv("CONTEXT_COMPACT_SUMMARY_INPUT_FRACTION") or 0.6)

SUMMARY_BUDGET_TOKENS = max(1500, int(CONTENT_SIZE * SUMMARY_BUDGET_RATIO))
KEEP_TOKENS_CAP = max(2000, int(CONTENT_SIZE * KEEP_TOKENS_RATIO))

# Marker for summary messages: metadata key + content prefix (dual signal so the
# summary survives even if metadata is dropped by a storage layer).
SUMMARY_ROLE = "__role__"
SUMMARY_ROLE_VALUE = "context_summary"
SUMMARY_PREFIX = "【历史上下文摘要】"
LONG_HEAD = "## 长期记忆（早期历史，已定稿）"
RECENT_HEAD = "## 近期记忆（逐步补充）"

_CJK_RE = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)


# ---------------------------------------------------------------- estimation


def _count_cjk(text: str) -> int:
    return len(_CJK_RE.findall(text))


def estimate_text_tokens(text) -> float:
    """Token estimate calibrated against the local llama-server tokenizer.

    Chinese/Japanese/Korean ~1.85 chars/token, everything else (English, code,
    JSON, paths, numbers) ~4.0 chars/token. Non-string content (multimodal
    block lists) is flattened first so the estimate never raises.
    """
    if not text:
        return 0.0
    if not isinstance(text, str):
        text = _flatten_content(text)
    if not text:
        return 0.0
    cjk = _count_cjk(text)
    other = len(text) - cjk
    return cjk / 1.85 + other / 4.0


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """Rough token estimate for a message list (text + tool-call args + images).

    Mirrors estimate_text_tokens so the display and the threshold agree.
    Images carry no text tokens in the transcript but are far from free at
    inference time, so each image block is counted as IMAGE_TOKEN_ESTIMATE.
    """
    total = 0.0
    for m in messages or []:
        total += estimate_text_tokens(_msg_text(m))
        total += _count_image_blocks(getattr(m, "content", "")) * IMAGE_TOKEN_ESTIMATE
        for tc in getattr(m, "tool_calls", None) or []:
            args = tc.get("args", "") if isinstance(tc, dict) else getattr(tc, "args", "")
            total += estimate_text_tokens(str(args))
    return max(1, round(total))


def _count_image_blocks(content) -> int:
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for block in content
        if isinstance(block, dict) and block.get("type") in ("image_url", "image")
    )


def _flatten_content(content) -> str:
    """Flatten a message content (str or multimodal block list) into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        images = 0
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") in ("image_url", "image"):
                    images += 1
            elif isinstance(block, BaseMessage):
                parts.append(_flatten_content(block.content))
            elif isinstance(block, str):
                parts.append(block)
        parts.append("[图片]" * images)
        return "\n".join(p for p in parts if p)
    return "" if content is None else str(content)


def _msg_text(m: BaseMessage) -> str:
    """Extract plain text from a message content (str or content blocks)."""
    return _flatten_content(getattr(m, "content", ""))


def _role_label(m: BaseMessage) -> str:
    return type(m).__name__.replace("Message", "").lower() or "message"


# ---------------------------------------------------------------- summary text


def _is_summary(m: BaseMessage) -> bool:
    """True if the message is a compaction summary (metadata or prefix)."""
    meta = getattr(m, "metadata", None) or {}
    if isinstance(meta, dict) and meta.get(SUMMARY_ROLE) == SUMMARY_ROLE_VALUE:
        return True
    text = _msg_text(m)
    return bool(text and text.lstrip().startswith(SUMMARY_PREFIX))


def parse_summary(text: str) -> tuple[str, str]:
    """Split a stored summary into (long_term, recent).

    Legacy summaries (no section markers) are treated as long_term text.
    """
    body = (text or "").replace(SUMMARY_PREFIX, "", 1).strip()
    if LONG_HEAD not in body:
        return body, ""
    long_part = body.split(LONG_HEAD, 1)[1]
    if RECENT_HEAD in long_part:
        long_text, recent_text = long_part.split(RECENT_HEAD, 1)
    else:
        long_text, recent_text = long_part, ""
    return long_text.strip(), recent_text.strip()


def render_summary(long_text: str, recent_text: str) -> str:
    parts = [LONG_HEAD, long_text.strip() or "（无）"]
    parts += ["", RECENT_HEAD, recent_text.strip() or "（无）"]
    return "\n".join(parts)


def _prior_summary_texts(to_keep: list[BaseMessage]) -> tuple[str, str]:
    """Collect and merge the long/recent layers of every kept summary message."""
    longs, recents = [], []
    for m in to_keep:
        if not _is_summary(m):
            continue
        lt, rt = parse_summary(_msg_text(m))
        if lt:
            longs.append(lt)
        if rt:
            recents.append(rt)
    return "\n\n".join(longs).strip(), "\n\n".join(recents).strip()


# ---------------------------------------------------------------- splitting


def _task_anchor(messages: list[BaseMessage]) -> BaseMessage | None:
    """Earliest non-summary user message: the original task goal, kept verbatim."""
    for m in messages:
        if _is_summary(m):
            continue
        if isinstance(m, HumanMessage):
            return m
    return None


def _turn_starts(messages: list[BaseMessage]) -> list[int]:
    """Indices of user messages (turn boundaries) in the non-summary sequence."""
    return [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]


def split_compact(messages: list[BaseMessage]):
    """Split messages into (early-to-summarize, to-keep).

    - summary messages are never dropped (their layers are inherited);
    - the task-goal anchor (earliest user message) is never dropped;
    - the most recent KEEP_TURNS turns are kept verbatim, shrunk turn by turn
      while the kept part exceeds KEEP_TOKENS_CAP.
    """
    if not messages:
        return [], list(messages)
    summaries = [m for m in messages if _is_summary(m)]
    others = [m for m in messages if not _is_summary(m)]
    if not others:
        return [], list(messages)

    anchor = _task_anchor(others)
    starts = _turn_starts(others)
    if len(starts) <= KEEP_TURNS:
        # Fewer turns than the keep window: nothing old enough to summarize.
        return [], list(messages)

    turns = KEEP_TURNS
    cut = starts[-turns]
    recent = others[cut:]
    while turns > 1 and estimate_tokens(recent) > KEEP_TOKENS_CAP:
        turns -= 1
        cut = starts[-turns]
        recent = others[cut:]

    in_recent = any(m is anchor for m in recent)
    early = [m for m in others[:cut] if not (anchor is not None and m is anchor)]
    to_keep = summaries + ([] if in_recent or anchor is None else [anchor]) + recent
    return early, to_keep


# ---------------------------------------------------------------- summarizer

_SUMMARY_SECTIONS = [
    "任务目标：",
    "已完成：",
    "当前进度：",
    "关键事实与绝对路径：",
    "已做决策：",
    "待办与未解决：",
]


def _build_prompt(text: str) -> str:
    sections = "\n".join(f"- {s}" for s in _SUMMARY_SECTIONS)
    return (
        "你在为一个 AI Agent 压缩对话历史。压缩完成后，下面的原始内容将从上下文中删除，"
        "模型之后只能看到你输出的这份摘要；任何遗漏都会让后续任务失败。\n\n"
        "输出必须严格包含下面六个小节（沿用对话本身的语言，不要翻译）：\n"
        f"{sections}\n\n"
        "要求：\n"
        "- 「关键事实与绝对路径」必须逐条列出所有文件绝对路径、文件名、命令、错误信息、"
        "数值和结论；不得用「若干文件」「一些数据」之类的概括代替具体值。\n"
        "- 「已做决策」写清做过什么取舍、为什么。\n"
        "- 「待办与未解决」列出未完成事项、阻塞点和用户明确提出的要求。\n"
        "- 直接陈述事实，不要写「用户要求我…」这类空话；不要输出与本任务无关的套话。\n"
        f"- 总长度不超过 {SUMMARY_MAX_TOKENS} tokens。\n\n"
        f"<对话历史>\n{text}\n</对话历史>"
    )


_MERGE_PROMPT = (
    "下面是同一个任务的历史摘要，分为「长期记忆」和「近期记忆」两部分。"
    "请把它们合并成一份完整摘要：必须保留两部分中的每一条具体信息（文件路径、数值、"
    "决策、待办、错误信息），可以合并同类项，但绝不能删除任何条目。\n\n"
    "输出同样的六个小节：\n" + "\n".join(f"- {s}" for s in _SUMMARY_SECTIONS) + "\n\n"
    f"总长度不超过 {SUMMARY_MAX_TOKENS} tokens。\n\n"
    "<长期记忆>\n{long}\n</长期记忆>\n\n<近期记忆>\n{recent}\n</近期记忆>"
)


def format_messages_for_summary(messages: list[BaseMessage]) -> str:
    """Render messages as labeled lines for the summarizer prompt."""
    lines = []
    for m in messages:
        text = _msg_text(m)
        tcs = getattr(m, "tool_calls", None) or []
        if tcs:
            names = [t.get("name") if isinstance(t, dict) else getattr(t, "name", "?") for t in tcs]
            text = f"{text}\n[调用工具] {', '.join(str(n) for n in names)}".strip()
        if not text.strip():
            continue
        lines.append(f"[{_role_label(m)}] {text}")
    return "\n\n".join(lines)


def _extract_text(resp) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts).strip()
    return str(content).strip()


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split long text into chunks of at most max_chars chars (on paragraph gaps)."""
    if len(text) <= max_chars:
        return [text] if text.strip() else []
    chunks: list[str] = []
    cur = ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > max_chars and cur:
            chunks.append(cur.strip())
            cur = para
        else:
            cur = (cur + "\n\n" + para if cur else para)
        # 单个段落仍超限：硬切
        while len(cur) > max_chars:
            chunks.append(cur[:max_chars].strip())
            cur = cur[max_chars:]
    if cur.strip():
        chunks.append(cur.strip())
    return [c for c in chunks if c]


async def _summarize(messages: list[BaseMessage]) -> str:
    """Summarize messages into the six-section template; "" on failure."""
    text = format_messages_for_summary(messages)
    if not text.strip():
        return ""
    # 1.85 chars/token (CJK) is the conservative side: keep each chunk well
    # below the model window.
    max_chars = max(2000, int(CONTENT_SIZE * SUMMARY_INPUT_FRACTION * 1.6))
    try:
        model = create_model()
    except Exception:  # noqa: BLE001 - a model build failure must not drop history
        return ""
    parts: list[str] = []
    for chunk in _split_text(text, max_chars):
        try:
            resp = await model.ainvoke([HumanMessage(content=_build_prompt(chunk))])
            part = _extract_text(resp)
        except Exception:  # noqa: BLE001 - handled below: empty result => no compaction
            part = ""
        if part and "任务目标" not in part and len(chunk) > 400:
            # The model ignored the template: keep its text, it is still
            # information, but flag the shape for debugging.
            part = f"（摘要格式未完全遵循）\n{part}"
        if part:
            parts.append(part)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    # Multiple chunks: keep every chunk's full six sections (no merging here),
    # so mid-history decisions are not dropped. The budget merge handles size.
    return "\n\n".join(f"### 片段 {i + 1}\n{p}" for i, p in enumerate(parts))


async def _merge_layers(long_text: str, recent_text: str) -> str:
    """Merge the two summary layers into one; "" on failure (keep the old text)."""
    try:
        model = create_model()
        resp = await model.ainvoke(
            [HumanMessage(content=_MERGE_PROMPT.format(long=long_text or "（无）", recent=recent_text))]
        )
        return _extract_text(resp)
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- entrypoints


async def compact_messages_list(
    messages: list[BaseMessage], min_gain: int | None = None
) -> tuple[list[BaseMessage], dict]:
    """Pure-function compaction of a message list.

    Returns ``(compacted_list, result_dict)``. On any summarization failure the
    original list is returned untouched with ``ok=False`` (never drop history
    because of a bad summary).

    ``min_gain`` (threshold, not a value-to-check) guards against
    counter-productive compaction: if the early part being summarized is
    smaller than this many tokens, the summary would cost more than it saves,
    so the history is left alone (``reason="low_gain"``). Pass ``0`` to force
    a preview (used by the dryRun debug endpoint).
    """
    before_tokens = estimate_tokens(messages)
    early, to_keep = split_compact(messages)
    if not early:
        return list(messages), {
            "ok": False,
            "reason": "not_enough_messages",
            "before_tokens": before_tokens,
        }

    long_text, recent_text = _prior_summary_texts(to_keep)
    early_tokens = estimate_tokens(early)
    gain_threshold = MIN_COMPACT_GAIN if min_gain is None else min_gain
    if early_tokens < gain_threshold:
        return list(messages), {
            "ok": False,
            "reason": "low_gain",
            "before_tokens": before_tokens,
            "would_summarize_tokens": early_tokens,
            "min_gain": gain_threshold,
        }

    new_part = await _summarize(early)
    if not new_part.strip():
        return list(messages), {
            "ok": False,
            "reason": "summary_failed",
            "before_tokens": before_tokens,
            "would_summarize_tokens": early_tokens,
        }

    recent_text = (f"{recent_text}\n\n### 新增\n{new_part}".strip() if recent_text else new_part)
    merged = False
    if estimate_text_tokens(f"{long_text}\n{recent_text}") > SUMMARY_BUDGET_TOKENS:
        merged_text = await _merge_layers(long_text, recent_text)
        if merged_text:
            long_text, recent_text, merged = merged_text, "", True

    summary_body = render_summary(long_text, recent_text)
    summary_msg = HumanMessage(
        content=f"{SUMMARY_PREFIX}\n{summary_body}",
        metadata={SUMMARY_ROLE: SUMMARY_ROLE_VALUE},
    )
    # 旧 summary 已被新 summary 完整继承，避免重复
    compacted = [summary_msg] + [m for m in to_keep if not _is_summary(m)]
    after_tokens = estimate_tokens(compacted)
    return compacted, {
        "ok": True,
        "mode": "merge" if merged else "append",
        "summary": summary_body[:200],
        "summary_tokens": round(estimate_text_tokens(summary_body)),
        "summary_budget": SUMMARY_BUDGET_TOKENS,
        "removed": len(early),
        "removed_tokens": early_tokens,
        "kept": len([m for m in compacted if not _is_summary(m)]),
        "kept_turns": KEEP_TURNS,
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "min_gain": gain_threshold,
    }


async def compact_messages(agent, config, messages: list[BaseMessage]) -> dict:
    """Compact a session's message history in the checkpoint (legacy path)."""
    compacted, result = await compact_messages_list(messages)
    if not result.get("ok"):
        return result

    removals = [RemoveMessage(id=m.id) for m in messages if getattr(m, "id", None)]
    summary_msg = compacted[0]
    await agent.aupdate_state(
        config,
        {"messages": [*removals, summary_msg]},
        as_node="__start__",
    )
    return result


def should_compact(messages: list[BaseMessage]) -> bool:
    """Threshold check: estimated tokens + overhead >= CONTENT_SIZE * RATIO."""
    if not messages:
        return False
    tokens = estimate_tokens(messages) + COMPACT_OVERHEAD
    return tokens >= CONTENT_SIZE * COMPACT_RATIO


def next_compaction_plan(messages: list[BaseMessage]) -> dict:
    """Preview what the next compaction would do (used by the debug endpoint)."""
    early, to_keep = split_compact(messages)
    return {
        "would_compact": should_compact(messages) and bool(early),
        "tokens": estimate_tokens(messages),
        "overhead": COMPACT_OVERHEAD,
        "threshold": round(CONTENT_SIZE * COMPACT_RATIO),
        "pct": round(100 * estimate_tokens(messages) / CONTENT_SIZE, 1),
        "would_summarize": len(early),
        "would_summarize_tokens": estimate_tokens(early) if early else 0,
        "would_keep": len(to_keep),
        "keep_turns": KEEP_TURNS,
    }


def active_config() -> dict:
    """Effective compaction settings (for the debug endpoint / logs)."""
    return {
        "content_size": CONTENT_SIZE,
        "ratio": COMPACT_RATIO,
        "overhead": COMPACT_OVERHEAD,
        "threshold_tokens": round(CONTENT_SIZE * COMPACT_RATIO),
        "keep_turns": KEEP_TURNS,
        "keep_tokens_cap": KEEP_TOKENS_CAP,
        "keep_tokens_ratio": KEEP_TOKENS_RATIO,
        "summary_max_tokens": SUMMARY_MAX_TOKENS,
        "summary_budget_tokens": SUMMARY_BUDGET_TOKENS,
        "summary_budget_ratio": SUMMARY_BUDGET_RATIO,
        "summary_input_fraction": SUMMARY_INPUT_FRACTION,
        "min_gain": MIN_COMPACT_GAIN,
        "image_tokens": IMAGE_TOKEN_ESTIMATE,
    }
