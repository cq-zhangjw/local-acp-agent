import functools
import json
import os
import re
import time

import requests
from playwright.sync_api import Request, sync_playwright
from fastmcp import FastMCP

mcp = FastMCP("Powerautomate cloud MCP Server")

script_path = os.path.dirname(os.path.abspath(__file__))

PORTAL_URL = "https://make.powerautomate.com/"
TOKEN_FILE = os.path.join(script_path, "token.txt")
HTTP_TIMEOUT = 30
LOGIN_TIMEOUT_MS = 300000


def _safe(func):
    # Return errors as text: a raised exception makes the agent-side call hang.
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {type(exc).__name__}: {exc}"

    return wrapper


def get_environment_info(access_token: str) -> tuple[int, dict]:
    """Return (status_code, json) of the default Power Platform environment."""
    res = requests.get(
        "https://api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/environments/~default"
        "?api-version=2020-06-01&$expand=properties.permissions"
        "&$filter=minimumAppPermission%20eq%20%27CanEdit%27",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=HTTP_TIMEOUT,
    )
    try:
        return res.status_code, res.json()
    except ValueError:
        return res.status_code, {"text": res.text}


def _load_cached_token() -> str | None:
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            token = json.load(f)[0]["token"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    status, _ = get_environment_info(token)
    return token if status == 200 else None


def login(site_url: str = PORTAL_URL, user_data_dir: str | None = None, headless: bool = False) -> str:
    """
    Return a valid Power Automate access token.

    Reuses the cached token (token.txt) when still valid; otherwise opens Edge for an
    interactive login and captures the bearer token from api.flow.microsoft.com requests.
    """
    token = _load_cached_token()
    if token:
        return token

    if user_data_dir is not None:
        user_data_dir = os.path.abspath(user_data_dir)
    captured: list[dict] = []

    def request_handler(request: Request):
        if captured or "api.flow.microsoft.com" not in request.url:
            return
        headers = request.headers
        auth = headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            captured.append({"url": request.url, "token": auth.split(" ", 1)[1]})
        elif headers.get("x-access-token"):
            captured.append({"url": request.url, "token": headers["x-access-token"]})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            accept_downloads=True,
            headless=headless,
            slow_mo=100,
            channel="msedge",
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-features=TranslateUI",
            ],
            ignore_default_args=["--enable-automation"],
        )
        try:
            page = browser.new_page()
            page.on("request", request_handler)
            page.goto(site_url, timeout=LOGIN_TIMEOUT_MS)
            page.wait_for_url(re.compile(r"home/?$"), timeout=LOGIN_TIMEOUT_MS)
        finally:
            browser.close()

    if not captured:
        raise RuntimeError("Login finished but no Power Automate access token was captured.")
    status, data = get_environment_info(captured[0]["token"])
    if status != 200:
        raise RuntimeError(f"Captured token is invalid (status {status}): {data}")

    with open(TOKEN_FILE, mode="w", encoding="utf-8") as f:
        json.dump(captured, f, indent=2)
    return captured[0]["token"]


def _flows_url(access_token: str, path: str = "") -> str:
    status, env = get_environment_info(access_token)
    if status != 200:
        raise RuntimeError(f"Failed to get the default environment (status {status}): {env}")
    name = env["name"].replace("-", "").lower()
    return f"https://{name[:-2]}.{name[-2:]}.environment.api.powerplatform.com/powerautomate/flows{path}"


def _headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


@mcp.tool()
@_safe
def get_flow_list(top: int = 20) -> str:
    """
    List Power Automate cloud flows in the default environment and save the raw result to a JSON file.
    The first call may open Edge for interactive login.

    Args:
        top: Maximum number of flows to return. Defaults to 20.

    Returns:
        The saved JSON file path and the flow count. Read the file to get flow ids and names.
    """
    token = login()
    res = requests.get(
        _flows_url(token),
        params={"api-version": "1", "$top": top},
        headers=_headers(token),
        timeout=HTTP_TIMEOUT,
    )
    res.raise_for_status()
    data = res.json()

    save_dir = os.path.join(script_path, "tmp")
    os.makedirs(save_dir, exist_ok=True)
    save_file = os.path.join(save_dir, f"{int(time.time() * 1000)}.json")
    with open(save_file, mode="w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    count = len(data.get("value", [])) if isinstance(data, dict) else None
    return json.dumps({"save_file": save_file.replace("\\", "/"), "count": count}, ensure_ascii=False)


def get_flow_detail(flow_id: str, access_token: str) -> dict:
    """Return the detail JSON of a flow."""
    res = requests.get(
        _flows_url(access_token, f"/{flow_id}"),
        params={"api-version": "1"},
        headers=_headers(access_token),
        timeout=HTTP_TIMEOUT,
    )
    res.raise_for_status()
    return res.json()


@mcp.tool()
@_safe
def delete_flow(flow_id: str) -> str:
    """
    Permanently delete a Power Automate cloud flow. This cannot be undone:
    always confirm the target flow with the user before calling.

    Args:
        flow_id: The flow id (GUID), as returned by get_flow_list.

    Returns:
        A result message.
    """
    token = login()
    res = requests.delete(
        _flows_url(token, f"/{flow_id}"),
        params={"api-version": "1"},
        headers=_headers(token),
        timeout=HTTP_TIMEOUT,
    )
    res.raise_for_status()
    return f"The flow '{flow_id}' has been deleted."


@mcp.tool()
@_safe
def run_flow(flow_id: str) -> str:
    """
    Trigger a Power Automate cloud flow (flows with a manual/HTTP trigger).

    Args:
        flow_id: The flow id (GUID), as returned by get_flow_list.

    Returns:
        A result message.
    """
    token = login()
    detail = get_flow_detail(flow_id, token)
    trigger_uri = detail.get("properties", {}).get("flowTriggerUri")
    if not trigger_uri:
        raise RuntimeError(f"The flow '{flow_id}' has no 'flowTriggerUri' (it may not be manually triggerable).")

    res = requests.post(trigger_uri, headers=_headers(token), timeout=HTTP_TIMEOUT)
    res.raise_for_status()
    return f"The flow '{flow_id}' has been started."


if __name__ == "__main__":
    import sys

    sys.stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", closefd=False)
    sys.stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False)

    mcp.run(transport="stdio")
