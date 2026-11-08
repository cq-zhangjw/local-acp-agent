"""Headless web search via Playwright — scrapes Bing CN / Baidu result pages.

Free, no API key. Slower than a proper search API (3-6s per query) and
fragile against markup changes, but no quota and no cost.
"""
import sys
import traceback

from fastmcp import FastMCP

mcp = FastMCP("web_search_server")


def _search_bing(query: str, max_results: int) -> list[dict]:
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
        page.goto(f"https://cn.bing.com/search?q={query}", timeout=15000, wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        items = page.query_selector_all("li.b_algo")
        for it in items[:max_results]:
            try:
                a = it.query_selector("h2 a")
                title = a.inner_text().strip() if a else ""
                url = a.get_attribute("href") if a else ""
                snippet_el = it.query_selector(".b_caption p") or it.query_selector("p")
                snippet = snippet_el.inner_text().strip() if snippet_el else ""
                if title and url:
                    results.append({"title": title, "url": url, "snippet": snippet})
            except Exception:
                continue
        browser.close()
    return results


def _search_baidu(query: str, max_results: int) -> list[dict]:
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
        page.goto(f"https://www.baidu.com/s?wd={query}", timeout=15000, wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        items = page.query_selector_all("div.result, div.c-container")
        for it in items[:max_results]:
            try:
                a = it.query_selector("h3 a")
                title = a.inner_text().strip() if a else ""
                url = a.get_attribute("href") if a else ""
                snippet_el = it.query_selector(".c-abstract") or it.query_selector("[class*='abstract']")
                snippet = snippet_el.inner_text().strip() if snippet_el else ""
                if title and url:
                    results.append({"title": title, "url": url, "snippet": snippet})
            except Exception:
                continue
        browser.close()
    return results


@mcp.tool()
def search_web(query: str, engine: str = "bing", max_results: int = 8) -> str:
    """Search the web via headless browser scraping (Bing CN or Baidu).

    Free, no API key. Returns up to max_results entries with title/url/snippet.
    Use this when the user asks for up-to-date info, news, or anything beyond
    your training cutoff. Bing is default (less anti-bot); switch to "baidu"
    if Bing results are insufficient for Chinese queries.

    Args:
        query: search keywords, e.g. "2026年最新 AI Agent 框架对比"
        engine: "bing" (default, cn.bing.com) or "baidu"
        max_results: number of results to return (default 8, max 15)
    """
    try:
        max_results = max(1, min(int(max_results), 15))
        if engine == "baidu":
            results = _search_baidu(query, max_results)
        else:
            results = _search_bing(query, max_results)
        if not results:
            return f"No results for '{query}' (engine={engine}). Try the other engine."
        lines = [f"=== Web results for: {query} ({engine}) ==="]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
        return "\n\n".join(lines)
    except Exception:
        return f"Error: {traceback.format_exc()}"


if __name__ == "__main__":
    import sys
    sys.stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", closefd=False)
    sys.stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False)
    mcp.run(transport="stdio")
