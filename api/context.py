"""Manual context control API (backed by the session-context table).

POST /api/context/compact
    body: {"sessionId": "<acp thread_id>", "agent": "<agent_name>", "dryRun": bool}

    Compacts the session's context messages (summarize early turns + drop
    them) against the MooFile context table, and returns message + overhead
    token usage before/after so the frontend can refresh its display.
    With dryRun=true nothing is written: it only returns what the summary
    WOULD look like (useful for tuning the summarizer).

GET /api/context/usage?sessionId=...&agent=...&role=...&web_search=...&model=...
    Reports the model-side context usage based on the context table (the same
    source of truth the next prompt will actually feed the model), instead of
    the checkpoint.

GET /api/context/current?sessionId=...&format=html|json&maxChars=...&lang=...
    Debug view of the context the next prompt will feed the model: effective
    compaction settings, the stored layered summary, every message with its
    role/tokens/preview, and which messages the NEXT compaction would drop.
    `format=html` renders a browser-readable page (no auth needed on localhost).

Phase-2 endpoints (GET/PUT/DELETE message, rollback) extend this module using
utils/session_context.update_message / delete_message / rollback_last_turn.
"""

import asyncio
import html as _html
import json
import logging
import os

import aiosqlite
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from langchain_core.messages import convert_to_messages
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

import acp_agent as _acp
from utils.context_compactor import (
    CONTENT_SIZE,
    SUMMARY_PREFIX,
    active_config,
    compact_messages_list,
    estimate_text_tokens,
    estimate_tokens,
    next_compaction_plan,
    parse_summary,
)
from utils.mcp_loader import close_mcp_clients
from utils.session_context import (
    delete_message as sc_delete_message,
    get_context as sc_get_context,
    init_from_checkpoint as sc_init_from_checkpoint,
    save_context as sc_save_context,
    update_message as sc_update_message,
)
from utils.model_util import get_model_config

logger = logging.getLogger("api.context")
router = APIRouter()


def _usage_sync(
    session_id: str,
    agent_name: str,
    role_name: str = "",
    web_search: bool = False,
    model_name: str = "",
) -> dict:
    """Run the async _usage on a fresh event loop inside a worker thread."""
    return asyncio.run(_usage(session_id, agent_name, role_name, web_search, model_name))


def _compact_sync(
    session_id: str,
    agent_name: str,
    dry_run: bool = False,
    role_name: str = "",
    web_search: bool = False,
    model_name: str = "",
) -> dict:
    return asyncio.run(
        _compact(session_id, agent_name, dry_run, role_name, web_search, model_name)
    )


def _content_size_for_model(model_name: str = "") -> int:
    if not model_name:
        return CONTENT_SIZE
    config = get_model_config(model_name, raise_on_missing=False) or {}
    try:
        return int(config.get("content_size") or CONTENT_SIZE)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid content_size for model '{model_name}'") from exc


def _context_usage_breakdown(
    message_tokens: int,
    overhead: dict,
    content_size: int,
) -> dict:
    category_tokens = {
        "system_prompt": overhead["system_prompt"]["tokens"],
        "skills": overhead["skills"]["tokens"],
        "tools": overhead["tool_tokens"],
        "messages": message_tokens,
    }
    total_tokens = sum(category_tokens.values())
    category_tokens["total"] = total_tokens
    return {
        "tokens": total_tokens,
        "message_tokens": message_tokens,
        "overhead_tokens": overhead["total_tokens"],
        "pct": round(100 * total_tokens / content_size) if content_size else 0,
        "categories": {
            name: {
                "tokens": tokens,
                "pct": round(100 * tokens / content_size, 1) if content_size else 0,
            }
            for name, tokens in category_tokens.items()
        },
    }


async def _seed_from_checkpoint(session_id: str, agent, config) -> list[dict]:
    """Seed the context table from the checkpoint for a legacy session that has
    no table record yet; returns the stored to_json message list."""
    state = await agent.aget_state(config)
    values = state.values if hasattr(state, "values") else (state if isinstance(state, dict) else {})
    ckpt_msgs = list((values or {}).get("messages", []) or [])
    return sc_init_from_checkpoint(session_id, ckpt_msgs)


async def _compact(
    session_id: str,
    agent_name: str,
    dry_run: bool = False,
    role_name: str = "",
    web_search: bool = False,
    model_name: str = "",
) -> dict:
    os.environ["AGENT_NAME"] = agent_name or "default"
    conn = await aiosqlite.connect(_acp.DB_PATH)
    checkpointer = AsyncSqliteSaver(conn)
    agent = None
    try:
        context = _acp.AgentSessionContext(cwd=".", mode="default", model=None)
        agent = _acp.build_agent(context, checkpointer)
        config = {"configurable": {"thread_id": session_id}}

        messages_json = sc_get_context(session_id)
        if messages_json is None:
            # Legacy session without a table record: seed it once from checkpoint.
            messages_json = await _seed_from_checkpoint(session_id, agent, config)
        if not messages_json:
            return {"ok": False, "error": "empty_session"}

        objs = convert_to_messages(messages_json)
        before_message_tokens = estimate_tokens(objs)
        compacted, result = await compact_messages_list(objs, min_gain=0 if dry_run else None)
        after_message_tokens = estimate_tokens(compacted)
        overhead = _overhead_breakdown(agent_name, role_name, web_search)
        overhead_tokens = overhead["total_tokens"]
        content_size = _content_size_for_model(model_name)
        before_accounting = _context_usage_breakdown(
            before_message_tokens,
            overhead,
            content_size,
        )
        after_accounting = _context_usage_breakdown(
            after_message_tokens,
            overhead,
            content_size,
        )
        before_tokens = before_accounting["tokens"]
        after_tokens = after_accounting["tokens"]
        if dry_run:
            # Preview only: do not touch the table, so the caller can inspect
            # what the summarizer produced before committing.
            result.update({
                "dry_run": True,
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
                "before_message_tokens": before_message_tokens,
                "after_message_tokens": after_message_tokens,
                "overhead_tokens": overhead_tokens,
                "after_categories": after_accounting["categories"],
                "pct_before": round(100 * before_tokens / content_size),
                "pct_after": round(100 * after_tokens / content_size),
                "after_messages": [
                    {
                        "type": type(m).__name__,
                        "is_summary": getattr(m, "metadata", {}).get("__role__") == "context_summary"
                        if isinstance(getattr(m, "metadata", None), dict)
                        else False,
                        "tokens": round(estimate_text_tokens(_msg_text(m))),
                        "text": _msg_text(m)[:1500],
                    }
                    for m in compacted
                ],
            })
            result["summary"] = _msg_text(compacted[0])[:4000] if compacted else ""
            return result
        sc_save_context(session_id, compacted)
        result.update({
            "ok": True,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "before_message_tokens": before_message_tokens,
            "after_message_tokens": after_message_tokens,
            "overhead_tokens": overhead_tokens,
            "after_categories": after_accounting["categories"],
            "pct_before": round(100 * before_tokens / content_size),
            "pct_after": round(100 * after_tokens / content_size),
        })
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("compact failed for session=%s", session_id)
        return {"ok": False, "error": str(e)}
    finally:
        await close_mcp_clients()
        await conn.close()


async def _usage(
    session_id: str,
    agent_name: str,
    role_name: str = "",
    web_search: bool = False,
    model_name: str = "",
) -> dict:
    """Report context usage from the context table.

    The table is the single source of truth for what the next prompt feeds the
    model, so its token estimate is the consistent display value (compaction
    against the table is reflected immediately). Legacy sessions without a
    table record fall back to the checkpoint and seed the table once.
    """
    os.environ["AGENT_NAME"] = agent_name or "default"
    messages_json = sc_get_context(session_id)
    if messages_json is None:
        # Legacy session: seed the table from the checkpoint so both displays
        # converge on the table.
        conn = await aiosqlite.connect(_acp.DB_PATH)
        checkpointer = AsyncSqliteSaver(conn)
        try:
            context = _acp.AgentSessionContext(cwd=".", mode="default", model=None)
            agent = _acp.build_agent(context, checkpointer)
            config = {"configurable": {"thread_id": session_id}}
            messages_json = await _seed_from_checkpoint(session_id, agent, config)
        except Exception as e:  # noqa: BLE001
            logger.exception("usage failed for session=%s", session_id)
            return {"ok": False, "error": str(e)}
        finally:
            await close_mcp_clients()
            await conn.close()

    objs = convert_to_messages(messages_json) if messages_json else []
    msg_tokens = estimate_tokens(objs) if objs else 0
    overhead = _overhead_breakdown(agent_name, role_name, web_search)
    content_size = _content_size_for_model(model_name)
    accounting = _context_usage_breakdown(msg_tokens, overhead, content_size)
    return {
        "ok": True,
        **accounting,
        "overhead_breakdown": overhead,
        "total": content_size,
        "message_count": len(objs),
    }


@router.get("/api/context/usage")
async def context_usage(
    sessionId: str,
    agent: str = "default",
    role: str = "",
    web_search: bool = False,
    model: str = "",
):
    if not sessionId:
        return {"ok": False, "error": "missing sessionId"}
    # _usage may build an agent (legacy seeding); run it in a worker thread so
    # the blocking build_agent / MCP startup cannot stall the uvicorn loop.
    return await asyncio.to_thread(
        _usage_sync, sessionId, agent, role, web_search, model
    )


def _msg_text(m) -> str:
    """Flatten message content (str or multimodal block list) into plain text."""
    content = getattr(m, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    parts.append(str(block.get("text", "")))
                elif btype in ("image_url", "image"):
                    parts.append("[图片]")
                else:
                    parts.append(f"[{btype}]")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return str(content)


def _msg_is_summary(m) -> bool:
    meta = getattr(m, "metadata", None) or {}
    if isinstance(meta, dict) and meta.get("__role__") == "context_summary":
        return True
    text = _msg_text(m)
    return bool(text and text.lstrip().startswith(SUMMARY_PREFIX))


def _overhead_breakdown(
    agent_name: str,
    role_name: str = "",
    web_search: bool = False,
) -> dict:
    """Estimate the fixed overhead tokens that are NOT in the message list:
    system prompt + tool schemas. These are never compacted, but they DO count
    against content_size, so the debug view should show them explicitly.
    """
    # system prompt: use the same assembler the agent uses at runtime.
    sp_text = _acp.build_system_prompt(
        agent_name,
        role_name=role_name,
        web_search=web_search,
    ) or ""
    skills_text = f"# Available Skills\n{_acp._agent_skills_prompt(agent_name)}"
    skills_tokens = round(estimate_text_tokens(skills_text))
    prompt_tokens = round(estimate_text_tokens(sp_text))
    system_tokens = max(0, prompt_tokens - skills_tokens)
    system_preview = sp_text.replace(skills_text, "", 1).strip()

    tool_rows = []
    for tool, source in _acp.get_agent_tool_inventory(agent_name, web_search):
        args_schema = tool.args_schema
        if isinstance(args_schema, dict):
            schema = args_schema
        elif args_schema is None:
            schema = {}
        elif hasattr(args_schema, "model_json_schema"):
            schema = args_schema.model_json_schema()
        else:
            raise TypeError(f"Unsupported args schema for tool '{tool.name}'")
        description = tool.description or ""
        schema_text = json.dumps(schema, ensure_ascii=False)
        full = f"{tool.name}\n{description}\n{schema_text}"
        tool_rows.append({
            "name": tool.name,
            "source": source,
            "description": description,
            "tokens": round(estimate_text_tokens(full)),
        })

    tool_tokens = sum(t["tokens"] for t in tool_rows)
    total_tokens = prompt_tokens + tool_tokens
    return {
        "system_prompt": {
            "chars": len(system_preview),
            "tokens": system_tokens,
            "preview": system_preview,
            "truncated": False,
        },
        "skills": {
            "chars": len(skills_text),
            "tokens": skills_tokens,
            "preview": skills_text,
            "truncated": False,
        },
        "tools": tool_rows,
        "tool_count": len(tool_rows),
        "tool_tokens": tool_tokens,
        "prompt_tokens": prompt_tokens,
        "total_tokens": total_tokens,
    }


def _build_context_report(
    session_id: str,
    agent_name: str,
    max_chars: int,
    role_name: str = "",
    web_search: bool = False,
    model_name: str = "",
    lang: str = "en",
) -> dict:
    """Assemble the debug report for the context the next prompt will use."""
    messages_json = sc_get_context(session_id)
    if messages_json is None:
        return {
            "ok": False,
            "error": "no_context_record",
            "session_id": session_id,
            "lang": lang,
            "hint": _CONTEXT_LABELS.get(lang, _CONTEXT_LABELS["en"])["context_not_ready"],
        }
    objs = convert_to_messages(messages_json) if messages_json else []

    from utils.context_compactor import split_compact

    early, _to_keep = split_compact(objs)
    early_ids = {id(m) for m in early}

    rows = []
    total_chars = 0
    for i, m in enumerate(objs):
        text = _msg_text(m)
        total_chars += len(text)
        tcs = getattr(m, "tool_calls", None) or []
        tool_names = [
            (t.get("name") if isinstance(t, dict) else getattr(t, "name", "?")) for t in tcs
        ]
        rows.append({
            "index": i,
            "type": type(m).__name__,
            "is_summary": _msg_is_summary(m),
            "chars": len(text),
            "tokens": estimate_tokens([m]),
            "tool_calls": tool_names,
            "id": getattr(m, "id", None),
            "will_drop_next_compaction": id(m) in early_ids,
            "text": text if max_chars <= 0 else text[:max_chars],
            "truncated": max_chars > 0 and len(text) > max_chars,
        })

    longs, recents = [], []
    for m in objs:
        if _msg_is_summary(m):
            lt, rt = parse_summary(_msg_text(m))
            if lt:
                longs.append(lt)
            if rt:
                recents.append(rt)
    summary_long = "\n\n".join(longs)
    summary_recent = "\n\n".join(recents)

    message_tokens = estimate_tokens(objs) if objs else 0
    overhead = _overhead_breakdown(agent_name, role_name, web_search)
    content_size = _content_size_for_model(model_name)
    accounting = _context_usage_breakdown(message_tokens, overhead, content_size)
    return {
        "ok": True,
        "session_id": session_id,
        "agent": agent_name or "default",
        "config": active_config(),
        "lang": lang,
        "plan": next_compaction_plan(objs),
        "summary": {
            "long": summary_long,
            "recent": summary_recent,
            "tokens": round(estimate_text_tokens(f"{summary_long}\n{summary_recent}")),
        },
        "stats": {
            "message_count": len(objs),
            "chars": total_chars,
            **accounting,
            "content_size": content_size,
        },
        "messages": rows,
        "overhead": overhead,
    }


_CONTEXT_LABELS = {
    "zh": {
        "unavailable": "无法读取上下文",
        "context_not_ready": "该会话还没有上下文记录（首次发送消息后才会生成）",
        "long_memory": "长期记忆（已定稿，压缩时不再重写）",
        "recent_memory": "近期记忆（每次压缩追加）",
        "empty": "（空）",
        "overhead": "Overhead 组成（系统提示词 + 技能 + 工具 schema，约 {tokens} tokens，{count} 个工具）— 永不压缩",
        "system_prompt": "系统提示词",
        "skills": "技能",
        "tool_schema": "工具 schema（{count} 个）",
        "tools": "工具",
        "type": "类型",
        "tool_name": "工具名",
        "source": "来源",
        "tokens": "tokens",
        "description": "描述",
        "summary": "摘要",
        "drop": "下次压缩将删除",
        "keep": "保留",
        "page_title": "ACPAgent 当前上下文",
        "header": "当前上下文（下一次请求将发送给模型的内容）",
        "session": "会话",
        "agent": "Agent",
        "separator": "：",
        "prompt_note": "消息列表 + 下方 Overhead 组成（系统提示词、技能和工具 schema）合起来才是完整 prompt",
        "usage": "上下文用量",
        "message_count": "消息数",
        "threshold": "压缩阈值",
        "ratio": "比例 {ratio} + overhead {overhead}",
        "next_compaction": "下次压缩",
        "will_compact": "会触发",
        "will_not_compact": "不会触发",
        "will_summarize": "将压缩",
        "if_triggered": "（若触发）将压缩",
        "items": "条",
        "tokens_suffix": "tokens",
        "keep_recent": "最近 {turns} 轮",
        "compaction_detail": "{action} {count} {items} / {tokens} tokens，保留 {keep} {items}（{recent}）",
        "summary_usage": "摘要用量",
        "budget": "预算 {tokens} tokens",
        "layered_summary": "分层摘要",
        "messages": "消息列表",
        "message_legend": "（红色 = 下次压缩会删除；蓝色 = 摘要消息）",
        "state": "状态",
        "characters": "字符",
        "tool_calls": "工具调用",
        "preview": "内容预览",
        "truncated": "（已截断）",
        "truncate_note": "限长预览：加 <code>&amp;maxChars=0</code> 显示全文；<code>&amp;format=json</code> 返回原始数据；POST /api/context/compact 带 <code>{\"dryRun\": true}</code> 可预览压缩结果而不写库。",
        "subagent": "子 Agent",
        "mcp": "MCP",
        "builtin": "内置",
    },
    "ja": {
        "unavailable": "コンテキストを読み込めません",
        "context_not_ready": "このセッションにはまだコンテキスト記録がありません（最初のメッセージ送信後に作成されます）",
        "long_memory": "長期メモリ（確定済み、圧縮時に書き換えません）",
        "recent_memory": "最近のメモリ（圧縮ごとに追加）",
        "empty": "（空）",
        "overhead": "Overhead（システムプロンプト + スキル + ツール schema、約 {tokens} tokens、{count} ツール）— 圧縮対象外",
        "system_prompt": "システムプロンプト",
        "skills": "スキル",
        "tool_schema": "ツール schema（{count} 件）",
        "tools": "ツール",
        "type": "タイプ",
        "tool_name": "ツール名",
        "source": "ソース",
        "tokens": "tokens",
        "description": "説明",
        "summary": "要約",
        "drop": "次回の圧縮で削除",
        "keep": "保持",
        "page_title": "ACPAgent 現在のコンテキスト",
        "header": "現在のコンテキスト（次のリクエストでモデルに送信される内容）",
        "session": "セッション",
        "agent": "Agent",
        "separator": "：",
        "prompt_note": "メッセージ一覧と下の Overhead（システムプロンプト、スキル、ツール schema）が完全な prompt を構成します",
        "usage": "コンテキスト使用量",
        "message_count": "メッセージ数",
        "threshold": "圧縮しきい値",
        "ratio": "比率 {ratio} + overhead {overhead}",
        "next_compaction": "次回の圧縮",
        "will_compact": "実行されます",
        "will_not_compact": "実行されません",
        "will_summarize": "要約対象",
        "if_triggered": "（実行時）要約対象",
        "items": "件",
        "tokens_suffix": "tokens",
        "keep_recent": "直近 {turns} ターン",
        "compaction_detail": "{action} {count} {items} / {tokens} tokens、{keep} {items}を保持（{recent}）",
        "summary_usage": "要約の使用量",
        "budget": "予算 {tokens} tokens",
        "layered_summary": "階層化された要約",
        "messages": "メッセージ一覧",
        "message_legend": "（赤 = 次回の圧縮で削除、青 = 要約メッセージ）",
        "state": "状態",
        "characters": "文字数",
        "tool_calls": "ツール呼び出し",
        "preview": "内容プレビュー",
        "truncated": "（省略）",
        "truncate_note": "プレビュー制限：<code>&amp;maxChars=0</code> で全文、<code>&amp;format=json</code> で JSON を表示。POST /api/context/compact に <code>{\"dryRun\": true}</code> を指定すると保存せずに圧縮結果を確認できます。",
        "subagent": "サブ Agent",
        "mcp": "MCP",
        "builtin": "組み込み",
    },
    "en": {
        "unavailable": "Unable to read context",
        "context_not_ready": "No context record exists yet (it is created after the first message).",
        "long_memory": "Long-term memory (finalized; not rewritten during compaction)",
        "recent_memory": "Recent memory (appended on each compaction)",
        "empty": "(empty)",
        "overhead": "Overhead (system prompt + skills + tool schemas, about {tokens} tokens, {count} tools) — never compacted",
        "system_prompt": "System prompt",
        "skills": "Skills",
        "tool_schema": "Tool schemas ({count})",
        "tools": "Tools",
        "type": "Type",
        "tool_name": "Tool name",
        "source": "Source",
        "tokens": "tokens",
        "description": "Description",
        "summary": "Summary",
        "drop": "Will be removed at next compaction",
        "keep": "Kept",
        "page_title": "ACPAgent current context",
        "header": "Current context (content sent to the model on the next request)",
        "session": "Session",
        "agent": "Agent",
        "separator": ":",
        "prompt_note": "The message list plus the Overhead below (system prompt, skills, and tool schemas) make up the complete prompt",
        "usage": "Context usage",
        "message_count": "Messages",
        "threshold": "Compaction threshold",
        "ratio": "ratio {ratio} + overhead {overhead}",
        "next_compaction": "Next compaction",
        "will_compact": "Will run",
        "will_not_compact": "Will not run",
        "will_summarize": "Will summarize",
        "if_triggered": "Would summarize",
        "items": "items",
        "tokens_suffix": "tokens",
        "keep_recent": "last {turns} turns",
        "compaction_detail": "{action} {count} {items} / {tokens} tokens; keep {keep} {items} ({recent})",
        "summary_usage": "Summary usage",
        "budget": "budget {tokens} tokens",
        "layered_summary": "Layered summary",
        "messages": "Messages",
        "message_legend": "(Red = removed at next compaction; blue = summary message)",
        "state": "State",
        "characters": "Characters",
        "tool_calls": "Tool calls",
        "preview": "Content preview",
        "truncated": "(truncated)",
        "truncate_note": "Limited preview: add <code>&amp;maxChars=0</code> for full text; use <code>&amp;format=json</code> for raw data. POST /api/context/compact with <code>{\"dryRun\": true}</code> previews compaction without saving.",
        "subagent": "Subagent",
        "mcp": "MCP",
        "builtin": "Built-in",
    },
}


def _html_context_report(report: dict) -> str:
    """Render the report as a plain browser-readable page."""
    lang = report.get("lang", "en")
    if lang not in _CONTEXT_LABELS:
        lang = "en"
    labels = _CONTEXT_LABELS[lang]
    if not report.get("ok"):
        return (
            f"<html lang='{lang}'><meta charset='utf-8'><title>{labels['page_title']}</title>"
            "<body style='font-family:system-ui;padding:32px'>"
            f"<h2>{labels['unavailable']}</h2><p>{_html.escape(str(report.get('error')))}</p>"
            f"<p style='color:#666'>{_html.escape(str(report.get('hint') or ''))}</p></body></html>"
        )

    cfg = report["config"]
    plan = report["plan"]
    stats = report["stats"]
    summary = report["summary"]

    def esc(t):
        return _html.escape(str(t or ""))

    def pct_class(pct):
        if pct >= 85:
            return "#dc2626"
        if pct >= 60:
            return "#d97706"
        return "#059669"

    compaction_detail = labels["compaction_detail"].format(
        action=labels["will_summarize"] if plan["would_compact"] else labels["if_triggered"],
        count=plan["would_summarize"],
        tokens=plan["would_summarize_tokens"],
        keep=plan["would_keep"],
        items=labels["items"],
        recent=labels["keep_recent"].format(turns=plan["keep_turns"]),
    )

    summary_block = (
        "<div style='display:flex;gap:16px;flex-wrap:wrap'>"
        f"<div style='flex:1 1 380px;min-width:300px;background:#fff;border:1px solid #e5e7eb;"
        "border-radius:10px;padding:14px 16px'>"
        f"<div style='font-size:13px;color:#6b7280;margin-bottom:6px'>{labels['long_memory']}</div>"
        f"<pre style='white-space:pre-wrap;word-break:break-word;margin:0;font-size:13px;line-height:1.6;"
        f"max-height:340px;overflow:auto'>{esc(summary['long'] or labels['empty'])}</pre></div>"
        f"<div style='flex:1 1 380px;min-width:300px;background:#fff;border:1px solid #e5e7eb;"
        "border-radius:10px;padding:14px 16px'>"
        f"<div style='font-size:13px;color:#6b7280;margin-bottom:6px'>{labels['recent_memory']}</div>"
        f"<pre style='white-space:pre-wrap;word-break:break-word;margin:0;font-size:13px;line-height:1.6;"
        f"max-height:340px;overflow:auto'>{esc(summary['recent'] or labels['empty'])}</pre></div></div>"
    )

    oh = report.get("overhead") or {}
    oh_sp = oh.get("system_prompt") or {}
    oh_skills = oh.get("skills") or {}
    oh_tools = oh.get("tools") or []
    category_labels = {
        "system_prompt": labels["system_prompt"],
        "skills": labels["skills"],
        "tools": labels["tools"],
        "messages": labels["messages"],
        "total": labels["usage"],
    }
    category_cards = "".join(
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 14px'>"
        f"<div style='font-size:12px;color:#6b7280'>{esc(category_labels[name])}</div>"
        f"<div style='font-size:18px;font-weight:600;color:{pct_class(category['pct'])}'>"
        f"{category['tokens']} <span style='font-size:12px;color:#6b7280;font-weight:400'>"
        f"tokens · {category['pct']}%</span></div></div>"
        for name, category in stats["categories"].items()
        if name != "total"
    )
    source_labels = {
        "builtin": labels["builtin"],
        "mcp": labels["mcp"],
        "subagent": labels["subagent"],
    }
    tool_rows_html = "".join(
        f"<tr><td style='padding:4px 8px;font-family:ui-monospace,Consolas,monospace;"
        f"font-size:12px;color:#2563eb'>{esc(t['name'])}</td>"
        f"<td style='padding:4px 8px;font-size:12px;color:#6b7280'>{esc(source_labels.get(t['source'], t['source']))}</td>"
        f"<td style='padding:4px 8px;text-align:right;font-size:12px;color:#6b7280'>{t['tokens']}</td>"
        f"<td style='padding:4px 8px;font-size:12px;color:#374151;word-break:break-word'>"
        f"{esc(t['description'][:160])}</td></tr>"
        for t in oh_tools
    )
    overhead_block = (
        "<details style='margin:20px 0;background:#fff;border:1px solid #e5e7eb;"
        "border-radius:10px;padding:12px 16px'>"
        f"<summary style='cursor:pointer;font-weight:600;font-size:14px'>"
        f"{labels['overhead'].format(tokens=oh.get('total_tokens', 0), count=oh.get('tool_count', 0))}</summary>"
        "<div style='margin-top:12px'>"
        "<details open style='margin-bottom:12px'>"
        f"<summary style='cursor:pointer;font-size:13px;color:#374151;font-weight:600'>"
        f"{labels['system_prompt']}（{oh_sp.get('tokens',0)} tokens）</summary>"
        f"<pre style='white-space:pre-wrap;word-break:break-word;margin:8px 0 0;"
        f"font-size:12px;line-height:1.6;background:#f8fafc;"
        f"padding:10px 12px;border-radius:6px'>{esc(oh_sp.get('preview') or labels['empty'])}</pre>"
        "</details>"
        "<details style='margin-bottom:12px'>"
        f"<summary style='cursor:pointer;font-size:13px;color:#374151;font-weight:600'>"
        f"{labels['skills']}（{oh_skills.get('tokens', 0)} tokens）</summary>"
        f"<pre style='white-space:pre-wrap;word-break:break-word;margin:8px 0 0;"
        f"font-size:12px;line-height:1.6;background:#f8fafc;"
        f"padding:10px 12px;border-radius:6px'>{esc(oh_skills.get('preview') or labels['empty'])}</pre>"
        "</details>"
        "<details>"
        f"<summary style='cursor:pointer;font-size:13px;color:#374151;font-weight:600'>"
        f"{labels['tool_schema'].format(count=oh.get('tool_count', 0))}（{oh.get('tool_tokens', 0)} tokens）</summary>"
        "<table style='border-collapse:collapse;width:100%;font-size:12px;margin-top:8px'>"
        "<thead><tr style='background:#f1f5f9'>"
        f"<th style='padding:6px 8px;text-align:left'>{labels['tool_name']}</th>"
        f"<th style='padding:6px 8px;text-align:left'>{labels['source']}</th>"
        f"<th style='padding:6px 8px;text-align:right'>{labels['tokens']}</th>"
        f"<th style='padding:6px 8px;text-align:left'>{labels['description']}</th></tr></thead>"
        f"<tbody>{tool_rows_html}</tbody></table>"
        "</details>"
        "</div></details>"
    )

    rows = []
    for m in report["messages"]:
        is_sum = m["is_summary"]
        drop = m["will_drop_next_compaction"]
        if is_sum:
            bg, tag = "#eff6ff", labels["summary"]
        elif drop:
            bg, tag = "#fef2f2", labels["drop"]
        else:
            bg, tag = "#ffffff", labels["keep"]
        tools = ", ".join(m["tool_calls"]) if m["tool_calls"] else ""
        truncated_badge = (
            f" <span style='color:#9ca3af'>…{labels['truncated']}</span>"
            if m["truncated"]
            else ""
        )
        rows.append(
            f"<tr style='background:{bg}'>"
            f"<td style='padding:6px 8px;color:#999'>{m['index']}</td>"
            f"<td style='padding:6px 8px;white-space:nowrap'>{esc(m['type'])}</td>"
            f"<td style='padding:6px 8px;color:#6b7280;font-size:12px'>{tag}</td>"
            f"<td style='padding:6px 8px;text-align:right;color:#6b7280'>{m['chars']}</td>"
            f"<td style='padding:6px 8px;text-align:right;color:#6b7280'>{m['tokens']}</td>"
            f"<td style='padding:6px 8px;color:#2563eb;font-size:12px'>{esc(tools)}</td>"
            f"<td style='padding:6px 8px;font-family:ui-monospace,Consolas,monospace;font-size:12px;"
            f"word-break:break-word;max-width:640px'>{esc(m['text'])}"
            f"{truncated_badge}</td>"
            "</tr>"
        )

    return (
        f"<html lang='{lang}'><meta charset='utf-8'><title>{labels['page_title']}</title>"
        "<body style='margin:0;padding:24px 28px;background:#f8fafc;color:#111827;"
        "font-family:system-ui,-apple-system,\"Segoe UI\",sans-serif'>"
        f"<h2 style='margin:0 0 4px'>{labels['header']}</h2>"
        f"<div style='color:#6b7280;font-size:13px;margin-bottom:18px'>{labels['session']}{labels['separator']} "
        f"<code>{esc(report['session_id'])}</code> · {labels['agent']}{labels['separator']} {esc(report['agent'])}"
        f" · {labels['prompt_note']}</div>"

        "<div style='display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px'>"
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px'>"
        f"<div style='font-size:12px;color:#6b7280'>{labels['usage']}</div>"
        f"<div style='font-size:22px;font-weight:600;color:{pct_class(stats['pct'])}'>"
        f"{stats['pct']}% <span style='font-size:12px;color:#6b7280;font-weight:400'>"
        f"{stats['tokens']} / {stats['content_size']} tokens</span></div></div>"
        f"{category_cards}"
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px'>"
        f"<div style='font-size:12px;color:#6b7280'>{labels['message_count']}</div>"
        f"<div style='font-size:22px;font-weight:600'>{stats['message_count']}</div></div>"
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px'>"
        f"<div style='font-size:12px;color:#6b7280'>{labels['threshold']}</div>"
        f"<div style='font-size:22px;font-weight:600'>{plan['threshold']}</div>"
        f"<div style='font-size:12px;color:#6b7280'>{labels['ratio'].format(ratio=cfg['ratio'], overhead=cfg['overhead'])}</div></div>"
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px'>"
        f"<div style='font-size:12px;color:#6b7280'>{labels['next_compaction']}</div>"
        f"<div style='font-size:22px;font-weight:600;color:{'#dc2626' if plan['would_compact'] else '#6b7280'}'>"
        f"{labels['will_compact'] if plan['would_compact'] else labels['will_not_compact']}</div>"
        f"<div style='font-size:12px;color:#6b7280'>"
        f"{compaction_detail}"
        "</div></div>"
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px'>"
        f"<div style='font-size:12px;color:#6b7280'>{labels['summary_usage']}</div>"
        f"<div style='font-size:22px;font-weight:600'>{summary['tokens']}</div>"
        f"<div style='font-size:12px;color:#6b7280'>{labels['budget'].format(tokens=cfg['summary_budget_tokens'])}</div></div>"
        "</div>"

        f"<h3 style='margin:20px 0 8px;font-size:15px'>{labels['layered_summary']}</h3>"
        f"{summary_block}"
        f"{overhead_block}"

        f"<h3 style='margin:22px 0 8px;font-size:15px'>{labels['messages']}"
        f"<span style='font-size:12px;color:#6b7280;font-weight:400'>{labels['message_legend']}</span></h3>"
        "<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:auto;max-height:70vh'>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
        "<thead style='position:sticky;top:0;background:#f1f5f9;z-index:1'>"
        f"<tr><th style='padding:8px;text-align:left'>#</th><th style='padding:8px;text-align:left'>{labels['type']}</th>"
        f"<th style='padding:8px;text-align:left'>{labels['state']}</th><th style='padding:8px;text-align:right'>{labels['characters']}</th>"
        f"<th style='padding:8px;text-align:right'>{labels['tokens']}</th>"
        f"<th style='padding:8px;text-align:left'>{labels['tool_calls']}</th>"
        f"<th style='padding:8px;text-align:left'>{labels['preview']}</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        "<div style='color:#9ca3af;font-size:12px;margin-top:14px'>"
        f"{labels['truncate_note']}</div>"
        "</body></html>"
    )


@router.get("/api/context/current")
async def context_current(
    sessionId: str,
    agent: str = "default",
    format: str = "json",
    maxChars: int = 500,
    role: str = "",
    web_search: bool = False,
    model: str = "",
    lang: str = "en",
):
    """Debug view: the exact context the next prompt will feed the model."""
    if not sessionId:
        return {"ok": False, "error": "missing sessionId"}
    report = await asyncio.to_thread(
        _build_context_report,
        sessionId,
        agent,
        max(0, int(maxChars)),
        role,
        web_search,
        model,
        lang,
    )
    if format.lower() == "html":
        return HTMLResponse(_html_context_report(report))
    return report


@router.delete("/api/context/{session_id}")
def delete_context(session_id: str):
    """Delete a session everywhere it is persisted.

    Removes the context-table record (db/db_session_content_*) and the
    langgraph checkpoint thread (agent_state.sqlite), so no stale state
    survives. Idempotent: returns ok even if the session was already gone.
    """
    if not session_id:
        return {"ok": False, "error": "missing session_id"}
    from utils.session_context import delete_session

    try:
        removed = delete_session(session_id)
    except Exception:  # noqa: BLE001 - deletion must never 500 the gateway
        logger.exception("[session_context] delete failed for %s", session_id)
        return {"ok": False, "error": "delete failed"}
    return {"ok": True, "removed": removed}


@router.get("/api/context/{session_id}/messages")
async def list_context_messages(session_id: str):
    """Return the session's context-table messages (to_json list, each with
    kwargs.id). The frontend uses this to resolve its display messages to the
    ACP message ids required by PUT/DELETE /messages/{message_id}."""
    messages = sc_get_context(session_id)
    if messages is None:
        return {"ok": False, "error": "no_context_record"}
    return {"ok": True, "messages": messages}


@router.put("/api/context/{session_id}/messages/{message_id}")
async def edit_context_message(session_id: str, message_id: str, body: dict):
    """Edit one message in the session's context table.

    Two body shapes are accepted:
    - full message object (BaseMessage.to_json() shape); its kwargs.id must
      match `message_id` (or be absent, in which case it is patched in)
    - `{"content": "<new text>"}`: the backend locates the message by id and
      replaces only its content (the frontend cannot see to_json structures)

    The session's sqlite checkpoint mirror is invalidated so the edit takes
    effect on the next prompt.
    """
    if not body:
        return {"ok": False, "error": "missing message body"}
    target_id = str(body.get("kwargs", {}).get("id") or body.get("id") or "")
    if target_id and target_id != message_id:
        return {"ok": False, "error": "message_id mismatch"}
    if "content" in body and not target_id:
        # Simplified mode: replace content only. Rebuild a full to_json object
        # from the stored message so update_message keeps every other field.
        stored = sc_get_context(session_id) or []
        found = None
        for m in stored:
            mid = str((m.get("kwargs") or {}).get("id") or m.get("id") or "")
            if mid == message_id:
                found = m
                break
        if found is None:
            return {"ok": False, "error": "message not found or session missing"}
        import copy

        full = copy.deepcopy(found)
        (full.setdefault("kwargs", {}))["content"] = body["content"]
        ok = sc_update_message(session_id, full)
    else:
        if not target_id:
            body.setdefault("kwargs", {})["id"] = message_id
        try:
            ok = sc_update_message(session_id, body)
        except Exception:  # noqa: BLE001
            logger.exception(
                "edit message failed for session=%s message=%s", session_id, message_id
            )
            return {"ok": False, "error": "edit failed"}
    if not ok:
        return {"ok": False, "error": "message not found or session missing"}
    return {"ok": True, "updated": True}


@router.delete("/api/context/{session_id}/messages/{message_id}")
async def delete_context_message(session_id: str, message_id: str):
    """Delete one message from the session's context table.

    The session's sqlite checkpoint mirror is invalidated so the deletion takes
    effect on the next prompt.
    """
    try:
        ok = sc_delete_message(session_id, message_id)
    except Exception:  # noqa: BLE001
        logger.exception("delete message failed for session=%s message=%s", session_id, message_id)
        return {"ok": False, "error": "delete failed"}
    if not ok:
        return {"ok": False, "error": "message not found or session missing"}
    return {"ok": True, "removed": True}


@router.post("/api/context/compact")
async def compact_context(body: dict):
    session_id = (body or {}).get("sessionId")
    agent_name = (body or {}).get("agent") or "default"
    dry_run = bool((body or {}).get("dryRun"))
    role_name = (body or {}).get("role") or ""
    web_search = bool((body or {}).get("web_search"))
    model_name = (body or {}).get("model") or ""
    if not session_id:
        return {"ok": False, "error": "missing sessionId"}
    # build_agent / MCP client startup is blocking and CPU-light but slow;
    # run it in a worker thread to avoid stalling the event loop.
    return await asyncio.to_thread(
        _compact_sync,
        session_id,
        agent_name,
        dry_run,
        role_name,
        web_search,
        model_name,
    )
