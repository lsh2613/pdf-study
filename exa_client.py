"""Exa Web Research MCP HTTP 클라이언트.

사용자에게는 노출되지 않는 내부 의존성. API key 불필요한 공개 엔드포인트를
streamablehttp_client로 호출한다. 실패 시 graceful degrade — sub-agent가
검색 결과 없이도 챕터 지식만으로 확장 문제를 만들 수 있도록 빈 결과 반환.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# Exa Web Research MCP의 공식 엔드포인트.
# 환경변수로 오버라이드 가능 (기업 프록시/사내 미러 사용 시).
DEFAULT_EXA_MCP_URL = "https://mcp.exa.ai/mcp"
EXA_TOOL_NAME = "web_search_exa"  # Exa의 검색 도구명

# 검색당 최대 결과 수
MAX_RESULTS = 5
# Sub-agent에 전달할 context 문자열 길이 상한
MAX_SNIPPET_CHARS = 800


def _endpoint() -> str:
    return os.environ.get("PDF_STUDY_EXA_MCP_URL", DEFAULT_EXA_MCP_URL)


def _normalize(result: Any) -> list[dict[str, Any]]:
    """Exa 응답을 [{title, url, snippet}, ...] 형태로 정규화."""
    items: list[dict[str, Any]] = []
    # Exa는 결과를 content/result 안 list[dict]로 줄 수 있음. 방어적 파싱.
    if result is None:
        return items

    candidates: list[Any]
    if isinstance(result, dict):
        for key in ("results", "data", "items"):
            v = result.get(key)
            if isinstance(v, list):
                candidates = v
                break
        else:
            candidates = [result]
    elif isinstance(result, list):
        candidates = result
    else:
        return items

    for c in candidates[:MAX_RESULTS]:
        if not isinstance(c, dict):
            continue
        title = str(c.get("title") or c.get("name") or "")[:200]
        url = str(c.get("url") or c.get("link") or "")
        snippet_raw = c.get("text") or c.get("snippet") or c.get("summary") or ""
        snippet = str(snippet_raw)[:MAX_SNIPPET_CHARS]
        if not url:
            continue
        items.append({"title": title, "url": url, "snippet": snippet})
    return items


async def search(query: str, num_results: int = MAX_RESULTS) -> dict[str, Any]:
    """Exa MCP 호출. 실패해도 raise하지 않고 빈 결과 + error 메시지로 반환.

    Returns:
        {"ok": bool, "error": str | None, "results": [{title,url,snippet}, ...]}
    """
    if not query or not query.strip():
        return {"ok": False, "error": "empty query", "results": []}

    url = _endpoint()
    num_results = max(1, min(int(num_results), MAX_RESULTS))

    try:
        # 지연 import — 서버 모듈 로딩 시 Exa 클라이언트가 없어도 죽지 않게.
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except Exception as e:
        logger.warning("mcp client import failed: %s", e)
        return {"ok": False, "error": f"mcp client unavailable: {e}", "results": []}

    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                # Exa Web Research MCP의 검색 도구 호출
                resp = await session.call_tool(
                    EXA_TOOL_NAME,
                    arguments={"query": query, "numResults": num_results},
                )
                # mcp 응답은 content (TextContent[]) 형태. JSON일 수도, 평문일 수도.
                payload = _extract_payload(resp)
                results = _normalize(payload)
                return {"ok": True, "error": None, "results": results}
    except Exception as e:
        logger.warning("Exa search failed (query=%r): %s", query, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "results": []}


def _extract_payload(resp: Any) -> Any:
    """mcp CallToolResult → 사용 가능한 list[dict] 파싱.

    Exa의 web_search_exa는 보통 다음 형식의 평문을 반환한다:
        Title: ...
        URL: ...
        Published: ...
        Author: ...
        Highlights:
        <본문 줄들...>

    여러 결과는 위 블록이 반복된다. JSON으로 오는 경우도 fallback 처리.
    """
    import json

    content = getattr(resp, "content", None)
    if content is None and isinstance(resp, dict):
        content = resp.get("content")
    if not content:
        return None

    texts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if text:
            texts.append(text)

    if not texts:
        return None

    joined = "\n".join(texts)

    # 1차: JSON 시도
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        pass

    # 2차: Exa 평문 블록 파싱
    return _parse_exa_plaintext(joined)


def _parse_exa_plaintext(text: str) -> list[dict[str, Any]]:
    """Title:/URL:/Highlights: 블록을 list[dict]로."""
    results: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_highlights = False
    highlight_lines: list[str] = []

    def flush() -> None:
        nonlocal current, highlight_lines, in_highlights
        if current is None:
            return
        if highlight_lines:
            current["snippet"] = "\n".join(highlight_lines).strip()
        results.append(current)
        current = None
        highlight_lines = []
        in_highlights = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        # 새 결과 시작
        if line.startswith("Title:"):
            flush()
            current = {"title": line[len("Title:"):].strip(), "url": "", "snippet": ""}
            in_highlights = False
            continue
        if current is None:
            continue
        if line.startswith("URL:"):
            current["url"] = line[len("URL:"):].strip()
            in_highlights = False
            continue
        if line.startswith("Published:") or line.startswith("Author:"):
            in_highlights = False
            continue
        if line.startswith("Highlights:"):
            in_highlights = True
            continue
        if in_highlights:
            highlight_lines.append(line)
    flush()

    return results
