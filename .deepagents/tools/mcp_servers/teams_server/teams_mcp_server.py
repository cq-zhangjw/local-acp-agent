import functools
import os

import requests
from dotenv import load_dotenv
from fastmcp import FastMCP

mcp = FastMCP("Teams MCP Server")

script_path = os.path.dirname(os.path.abspath(__file__))
# Optional default webhook (TEAMS_WEBHOOK_URL) kept out of source control.
load_dotenv(os.path.join(script_path, ".env"))

HTTP_TIMEOUT = 30


def _safe(func):
    # Return errors as text: a raised exception makes the agent-side call hang.
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {type(exc).__name__}: {exc}"

    return wrapper


def _legacy_payload(title: str, content: str) -> dict:
    # Retired O365 Connector (MessageCard) format, kept for webhook.office.com URLs.
    return {"@type": "MessageCard", "@context": "https://schema.org/extensions", "title": title, "text": content}


def _workflows_payload(title: str, content: str) -> dict:
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "msteams": {"width": "Full"},
                    "body": [
                        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
                        {"type": "TextBlock", "text": content, "wrap": True},
                    ],
                },
            }
        ],
    }


@mcp.tool()
@_safe
def send_msg_by_webhook(title: str, content: str, webhook_url: str | None = None) -> str:
    """
    Post a message card to a Microsoft Teams channel via a Teams Workflows webhook
    (legacy Incoming Webhook URLs are also accepted).

    Args:
        title: Title shown at the top of the message card.
        content: Message body. Basic Markdown only (bold, italic, lists, links); tables are not supported.
        webhook_url: Webhook URL. Optional; defaults to TEAMS_WEBHOOK_URL in the server's .env file.

    Returns:
        A confirmation message.
    """
    url = webhook_url or os.getenv("TEAMS_WEBHOOK_URL")
    if not url:
        raise ValueError("webhook_url is required (or set TEAMS_WEBHOOK_URL in the teams_server .env file).")

    legacy = "webhook.office.com" in url.lower()
    payload = _legacy_payload(title, content) if legacy else _workflows_payload(title, content)
    res = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
    if res.status_code >= 400:
        hint = " Legacy Incoming Webhooks are retired; create a Workflows webhook instead." if legacy else ""
        raise RuntimeError(f"Teams webhook returned {res.status_code}: {res.text[:300]}{hint}")
    return "The message has been sent to Teams."


if __name__ == "__main__":
    import sys

    sys.stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", closefd=False)
    sys.stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False)

    mcp.run(transport="stdio")
