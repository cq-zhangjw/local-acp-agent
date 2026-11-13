"""Manual context control API (backed by the session-context table).

POST /api/context/compact
    body: {"sessionId": "<acp thread_id>", "agent": "<agent_name>", "dryRun": bool}

    Compacts the session's context messages (summarize early turns + drop
    them) against the MooFile context table, and returns the estimated token
    usage before/after so the frontend can refresh its display.
    With dryRun=true nothing is written: it only returns what the summary
    WOULD look like (useful for tuning the summarizer).

GET /api/context/usage?sessionId=...&agent=...
    Reports the model-side context usage based on the context table (the same
    source of truth the next prompt will actually feed the model), instead of
    the checkpoint.

GET /api/context/current?sessionId=...&format=html|json&maxChars=...
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
from pathlib import Path

import aiosqlite
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from langchain_core.messages import convert_to_messages
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

import acp_agent as _acp
from utils.agent_registry import load_agents_config
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

logger = logging.getLogger("api.context")
router = APIRouter()


def _usage_sync(session_id: str, agent_name: str) -> dict:
    """Run the async _usage on a fresh event loop inside a worker thread."""
    return asyncio.run(_usage(session_id, agent_name))


def _compact_sync(session_id: str, agent_name: str, dry_run: bool = False) -> dict:
    return asyncio.run(_compact(session_id, agent_name, dry_run))


async def _seed_from_checkpoint(session_id: str, agent, config) -> list[dict]:
    """Seed the context table from the checkpoint for a legacy session that has
    no table record yet; returns the stored to_json message list."""
    state = await agent.aget_state(config)
    values = state.values if hasattr(state, "values") else (state if isinstance(state, dict) else {})
    ckpt_msgs = list((values or {}).get("messages", []) or [])
    return sc_init_from_checkpoint(session_id, ckpt_msgs)


async def _compact(session_id: str, agent_name: str, dry_run: bool = False) -> dict:
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
        before_tokens = estimate_tokens(objs)
        compacted, result = await compact_messages_list(objs, min_gain=0 if dry_run else None)
        after_tokens = estimate_tokens(compacted)
        if dry_run:
            # Preview only: do not touch the table, so the caller can inspect
            # what the summarizer produced before committing.
            result.update({
                "dry_run": True,
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
                "pct_before": round(100 * before_tokens / CONTENT_SIZE),
                "pct_after": round(100 * after_tokens / CONTENT_SIZE),
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
            "pct_before": round(100 * before_tokens / CONTENT_SIZE),
            "pct_after": round(100 * after_tokens / CONTENT_SIZE),
        })
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("compact failed for session=%s", session_id)
        return {"ok": False, "error": str(e)}
    finally:
        await close_mcp_clients()
        await conn.close()


async def _usage(session_id: str, agent_name: str) -> dict:
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
    overhead = _overhead_breakdown(agent_name)
    overhead_tokens = overhead.get("total_tokens", 0)
    total_tokens = msg_tokens + overhead_tokens
    return {
        "ok": True,
        "tokens": total_tokens,
        "message_tokens": msg_tokens,
        "overhead_tokens": overhead_tokens,
        "overhead_breakdown": overhead,
        "total": CONTENT_SIZE,
        "pct": round(100 * total_tokens / CONTENT_SIZE),
        "message_count": len(objs),
    }


@router.get("/api/context/usage")
async def context_usage(sessionId: str, agent: str = "default"):
    if not sessionId:
        return {"ok": False, "error": "missing sessionId"}
    # _usage may build an agent (legacy seeding); run it in a worker thread so
    # the blocking build_agent / MCP startup cannot stall the uvicorn loop.
    return await asyncio.to_thread(_usage_sync, sessionId, agent)


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


def _overhead_breakdown(agent_name: str, role_name: str = "") -> dict:
    """Estimate the fixed overhead tokens that are NOT in the message list:
    system prompt + tool schemas. These are never compacted, but they DO count
    against content_size, so the debug view should show them explicitly.
    """
    # system prompt: use the same assembler the agent uses at runtime.
    try:
        sp_text = _acp.build_system_prompt(agent_name, role_name=role_name, web_search=False) or ""
    except Exception:  # noqa: BLE001
        sp_text = ""
    sp_tokens = round(estimate_text_tokens(sp_text))

    # tools: built-in references + MCP metadata resolved from manifests
    tool_rows = []
    try:
        cfg = load_agents_config()
        agent_cfg = cfg.get(agent_name, {})
        tools_cfg = agent_cfg.get("tools", {})
        for t in tools_cfg.get("inner_tools", []) or []:
            tool_rows.append({
                "name": t, "source": "builtin",
                "description": "(deepagents 内置工具)",
                "tokens": round(estimate_text_tokens(t + " (built-in deepagents tool)")),
            })
        for mcp in tools_cfg.get("mcp_tools", []) or []:
            server = mcp.get("server", "?")
            manifest_path = (
                Path(__file__).resolve().parent.parent
                / ".deepagents"
                / "tools"
                / "mcp_servers"
                / server
                / "manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            available = {
                tool.get("name"): tool
                for tool in manifest.get("tools") or []
                if isinstance(tool, dict) and tool.get("name")
            }
            for selected in mcp.get("tools", []) or []:
                tool_name = selected if isinstance(selected, str) else selected.get("name")
                t = available.get(tool_name)
                if t is None:
                    continue
                desc = t.get("description") or ""
                schema = t.get("inputSchema") or {}
                full = f"mcp_{server}_{t.get('name','?')}\n{desc}\n{json.dumps(schema, ensure_ascii=False)}"
                tool_rows.append({
                    "name": f"mcp_{server}_{t.get('name','?')}",
                    "source": f"mcp:{server}",
                    "description": desc,
                    "tokens": round(estimate_text_tokens(full)),
                })
    except Exception:  # noqa: BLE001
        pass

    tool_tokens = sum(t["tokens"] for t in tool_rows)
    return {
        "system_prompt": {
            "chars": len(sp_text),
            "tokens": sp_tokens,
            "preview": sp_text,
            "truncated": False,
        },
        "tools": tool_rows,
        "tool_count": len(tool_rows),
        "total_tokens": sp_tokens + tool_tokens,
    }


def _build_context_report(session_id: str, agent_name: str, max_chars: int, role_name: str = "") -> dict:
    """Assemble the debug report for the context the next prompt will use."""
    messages_json = sc_get_context(session_id)
    if messages_json is None:
        return {
            "ok": False,
            "error": "no_context_record",
            "session_id": session_id,
            "hint": "该会话还没有上下文表记录（首次发送消息后才会生成）",
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
            "tokens": round(estimate_text_tokens(text)),
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

    tokens = estimate_tokens(objs) if objs else 0
    return {
        "ok": True,
        "session_id": session_id,
        "agent": agent_name or "default",
        "config": active_config(),
        "plan": next_compaction_plan(objs),
        "summary": {
            "long": summary_long,
            "recent": summary_recent,
            "tokens": round(estimate_text_tokens(f"{summary_long}\n{summary_recent}")),
        },
        "stats": {
            "message_count": len(objs),
            "chars": total_chars,
            "tokens": tokens,
            "content_size": CONTENT_SIZE,
            "pct": round(100 * tokens / CONTENT_SIZE, 1) if CONTENT_SIZE else 0,
        },
        "messages": rows,
        "overhead": _overhead_breakdown(agent_name, role_name),
    }


def _html_context_report(report: dict) -> str:
    """Render the report as a plain browser-readable page."""
    if not report.get("ok"):
        return (
            "<html><meta charset='utf-8'><title>context</title>"
            "<body style='font-family:system-ui;padding:32px'>"
            f"<h2>无法读取上下文</h2><p>{_html.escape(str(report.get('error')))}</p>"
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

    summary_block = (
        "<div style='display:flex;gap:16px;flex-wrap:wrap'>"
        f"<div style='flex:1 1 380px;min-width:300px;background:#fff;border:1px solid #e5e7eb;"
        "border-radius:10px;padding:14px 16px'>"
        "<div style='font-size:13px;color:#6b7280;margin-bottom:6px'>长期记忆（已定稿，压缩时不再重写）</div>"
        f"<pre style='white-space:pre-wrap;word-break:break-word;margin:0;font-size:13px;line-height:1.6;"
        f"max-height:340px;overflow:auto'>{esc(summary['long'] or '（空）')}</pre></div>"
        f"<div style='flex:1 1 380px;min-width:300px;background:#fff;border:1px solid #e5e7eb;"
        "border-radius:10px;padding:14px 16px'>"
        "<div style='font-size:13px;color:#6b7280;margin-bottom:6px'>近期记忆（每次压缩追加）</div>"
        f"<pre style='white-space:pre-wrap;word-break:break-word;margin:0;font-size:13px;line-height:1.6;"
        f"max-height:340px;overflow:auto'>{esc(summary['recent'] or '（空）')}</pre></div></div>"
    )

    # Overhead 组成：system prompt + 工具 schema（默认折叠，避免页面过长）
    oh = report.get("overhead") or {}
    oh_sp = oh.get("system_prompt") or {}
    oh_tools = oh.get("tools") or []
    tool_rows_html = "".join(
        f"<tr><td style='padding:4px 8px;font-family:ui-monospace,Consolas,monospace;"
        f"font-size:12px;color:#2563eb'>{esc(t['name'])}</td>"
        f"<td style='padding:4px 8px;font-size:12px;color:#6b7280'>{esc(t['source'])}</td>"
        f"<td style='padding:4px 8px;text-align:right;font-size:12px;color:#6b7280'>{t['tokens']}</td>"
        f"<td style='padding:4px 8px;font-size:12px;color:#374151;word-break:break-word'>"
        f"{esc(t['description'][:160])}</td></tr>"
        for t in oh_tools
    )
    overhead_block = (
        "<details style='margin:20px 0;background:#fff;border:1px solid #e5e7eb;"
        "border-radius:10px;padding:12px 16px'>"
        f"<summary style='cursor:pointer;font-weight:600;font-size:14px'>"
        f"Overhead 组成（system prompt + 工具 schema，约 {oh.get('total_tokens',0)} tokens，"
        f"{oh.get('tool_count',0)} 个工具）— 永不压缩</summary>"
        "<div style='margin-top:12px'>"
        # System prompt: separate collapsible, no truncation, no max-height
        "<details open style='margin-bottom:12px'>"
        f"<summary style='cursor:pointer;font-size:13px;color:#374151;font-weight:600'>"
        f"System prompt（{oh_sp.get('tokens',0)} tokens）</summary>"
        f"<pre style='white-space:pre-wrap;word-break:break-word;margin:8px 0 0;"
        f"font-size:12px;line-height:1.6;background:#f8fafc;"
        f"padding:10px 12px;border-radius:6px'>{esc(oh_sp.get('preview') or '（空）')}</pre>"
        "</details>"
        # Tools schema: separate collapsible
        "<details>"
        f"<summary style='cursor:pointer;font-size:13px;color:#374151;font-weight:600'>"
        f"工具 schema（{oh.get('tool_count',0)} 个）</summary>"
        "<table style='border-collapse:collapse;width:100%;font-size:12px;margin-top:8px'>"
        "<thead><tr style='background:#f1f5f9'>"
        "<th style='padding:6px 8px;text-align:left'>工具名</th>"
        "<th style='padding:6px 8px;text-align:left'>来源</th>"
        "<th style='padding:6px 8px;text-align:right'>tokens</th>"
        "<th style='padding:6px 8px;text-align:left'>描述</th></tr></thead>"
        f"<tbody>{tool_rows_html}</tbody></table>"
        "</details>"
        "</div></details>"
    )

    rows = []
    for m in report["messages"]:
        is_sum = m["is_summary"]
        drop = m["will_drop_next_compaction"]
        if is_sum:
            bg, tag = "#eff6ff", "摘要"
        elif drop:
            bg, tag = "#fef2f2", "下次压缩将删除"
        else:
            bg, tag = "#ffffff", "保留"
        tools = ", ".join(m["tool_calls"]) if m["tool_calls"] else ""
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
            f"{' <span style=\"color:#9ca3af\">…（已截断）</span>' if m['truncated'] else ''}</td>"
            "</tr>"
        )

    return (
        "<html><meta charset='utf-8'><title>ACPAgent 当前上下文</title>"
        "<body style='margin:0;padding:24px 28px;background:#f8fafc;color:#111827;"
        "font-family:system-ui,-apple-system,\"Segoe UI\",sans-serif'>"
        "<h2 style='margin:0 0 4px'>当前上下文（下一次请求真正喂给模型的内容）</h2>"
        f"<div style='color:#6b7280;font-size:13px;margin-bottom:18px'>session："
        f"<code>{esc(report['session_id'])}</code> · agent：{esc(report['agent'])}"
        " · 消息列表 + 下方 Overhead 组成（system prompt + 工具 schema）合起来才是完整 prompt</div>"

        "<div style='display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px'>"
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px'>"
        f"<div style='font-size:12px;color:#6b7280'>上下文用量</div>"
        f"<div style='font-size:22px;font-weight:600;color:{pct_class(stats['pct'])}'>"
        f"{stats['pct']}% <span style='font-size:12px;color:#6b7280;font-weight:400'>"
        f"{stats['tokens']} / {stats['content_size']} tokens</span></div></div>"
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px'>"
        f"<div style='font-size:12px;color:#6b7280'>消息数</div>"
        f"<div style='font-size:22px;font-weight:600'>{stats['message_count']}</div></div>"
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px'>"
        f"<div style='font-size:12px;color:#6b7280'>压缩阈值</div>"
        f"<div style='font-size:22px;font-weight:600'>{plan['threshold']}</div>"
        f"<div style='font-size:12px;color:#6b7280'>ratio {cfg['ratio']} + overhead {cfg['overhead']}</div></div>"
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px'>"
        f"<div style='font-size:12px;color:#6b7280'>下次压缩</div>"
        f"<div style='font-size:22px;font-weight:600;color:{'#dc2626' if plan['would_compact'] else '#6b7280'}'>"
        f"{'会触发' if plan['would_compact'] else '不会触发'}</div>"
        f"<div style='font-size:12px;color:#6b7280'>"
        f"{'将压缩' if plan['would_compact'] else '（若触发）将压缩'} {plan['would_summarize']} 条 / "
        f"{plan['would_summarize_tokens']} tokens，保留 {plan['would_keep']} 条（最近 {plan['keep_turns']} 轮）"
        "</div></div>"
        f"<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px'>"
        f"<div style='font-size:12px;color:#6b7280'>摘要用量</div>"
        f"<div style='font-size:22px;font-weight:600'>{summary['tokens']}</div>"
        f"<div style='font-size:12px;color:#6b7280'>预算 {cfg['summary_budget_tokens']} tokens</div></div>"
        "</div>"

        "<h3 style='margin:20px 0 8px;font-size:15px'>分层摘要</h3>"
        f"{summary_block}"
        f"{overhead_block}"

        "<h3 style='margin:22px 0 8px;font-size:15px'>消息列表"
        "<span style='font-size:12px;color:#6b7280;font-weight:400'>"
        "（红色 = 下次压缩会删除；蓝色 = 摘要消息）</span></h3>"
        "<div style='background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:auto;max-height:70vh'>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
        "<thead style='position:sticky;top:0;background:#f1f5f9;z-index:1'>"
        "<tr><th style='padding:8px;text-align:left'>#</th><th style='padding:8px;text-align:left'>类型</th>"
        "<th style='padding:8px;text-align:left'>状态</th><th style='padding:8px;text-align:right'>字符</th>"
        "<th style='padding:8px;text-align:right'>tokens</th>"
        "<th style='padding:8px;text-align:left'>工具调用</th>"
        "<th style='padding:8px;text-align:left'>内容预览</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        "<div style='color:#9ca3af;font-size:12px;margin-top:14px'>"
        "限长预览：加 <code>&amp;maxChars=0</code> 显示全文；<code>&amp;format=json</code> 返回原始数据；"
        "POST /api/context/compact 带 <code>{\"dryRun\": true}</code> 可预览压缩结果而不写库。</div>"
        "</body></html>"
    )


@router.get("/api/context/current")
async def context_current(
    sessionId: str,
    agent: str = "default",
    format: str = "json",
    maxChars: int = 500,
    role: str = "",
):
    """Debug view: the exact context the next prompt will feed the model."""
    if not sessionId:
        return {"ok": False, "error": "missing sessionId"}
    report = await asyncio.to_thread(
        _build_context_report, sessionId, agent, max(0, int(maxChars)), role
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
    if not session_id:
        return {"ok": False, "error": "missing sessionId"}
    # build_agent / MCP client startup is blocking and CPU-light but slow;
    # run it in a worker thread to avoid stalling the event loop.
    return await asyncio.to_thread(_compact_sync, session_id, agent_name, dry_run)
