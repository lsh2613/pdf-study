"""본문 텍스트에서 목차 후보를 찾는다.

전제: 텍스트는 reader.extract_page_text로 이미 노이즈 정리된 상태.
"목차" 등의 키워드 + 4가지 라인 패턴 매칭이 N개 이상이면 후보로 인정한다.
최종 검증은 메인 LLM의 책임.
"""
from __future__ import annotations

import re
from typing import Any

TOC_KEYWORDS = ("목차", "차례", "contents", "table of contents")

# (제목, 페이지번호) 추출용 4가지 패턴
TOC_LINE_PATTERNS = [
    re.compile(r"^(.+?)\s*\.{2,}\s*(\d+)\s*$"),          # "제목 .... 12"
    re.compile(r"^(.+?)\s{3,}(\d+)\s*$"),                 # "제목    12"
    re.compile(r"^(.+?)\s*[\(\[]\s*(\d+)\s*[\)\]]\s*$"),  # "제목 (12)" or "[12]"
    re.compile(r"^(.+?)\t+(\d+)\s*$"),                    # "제목\t12"
]

MIN_TOC_ENTRIES = 3  # 3개 이상 매칭 시 후보로 인정


def _has_toc_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(k in lowered for k in TOC_KEYWORDS)


def _match_line(line: str) -> tuple[str, int] | None:
    """한 줄이 TOC 라인 패턴과 매칭되는지."""
    stripped = line.strip()
    if not stripped or len(stripped) < 3:
        return None
    for pat in TOC_LINE_PATTERNS:
        m = pat.match(stripped)
        if m:
            title = m.group(1).strip()
            page = int(m.group(2))
            # 제목이 너무 짧거나 숫자만이면 거른다
            if len(title) < 2 or title.isdigit():
                return None
            return title, page
    return None


def find_toc_candidates(text: str) -> dict[str, Any]:
    """주어진 텍스트(보통 scan_pdf의 첫 N페이지)에서 목차 후보를 추출.

    Returns:
        {
            "has_toc_keyword": bool,
            "entries": [{"title": str, "page": int}, ...],
            "is_candidate": bool,   # entries >= MIN_TOC_ENTRIES
        }

    페이지 번호는 책에 인쇄된 번호 그대로 (PDF 페이지와 다를 수 있음).
    메인 LLM이 추후 매핑/보정한다.
    """
    has_keyword = _has_toc_keyword(text)

    entries: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for line in text.split("\n"):
        result = _match_line(line)
        if result is None:
            continue
        title, page = result
        # 동일 제목 중복 제거
        if title in seen_titles:
            continue
        seen_titles.add(title)
        entries.append({"title": title, "page": page})

    # 페이지 번호가 단조 증가하는 부분만 유지 (노이즈 라인 제거)
    entries = _filter_monotonic(entries)

    is_candidate = len(entries) >= MIN_TOC_ENTRIES

    return {
        "has_toc_keyword": has_keyword,
        "entries": entries,
        "is_candidate": is_candidate,
    }


def _filter_monotonic(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """페이지 번호가 단조 증가하는 가장 긴 연속 부분을 유지.

    목차 라인 패턴은 본문에서도 우연히 매칭될 수 있는데, 진짜 목차는
    페이지 번호가 단조 증가한다. 가장 긴 비감소 부분 시퀀스만 남긴다.
    """
    if len(entries) < 2:
        return entries

    n = len(entries)
    # LIS(비감소) DP — N이 작으므로 O(N^2)로 충분
    lengths = [1] * n
    prev = [-1] * n
    for i in range(1, n):
        for j in range(i):
            if entries[j]["page"] <= entries[i]["page"] and lengths[j] + 1 > lengths[i]:
                lengths[i] = lengths[j] + 1
                prev[i] = j

    end = max(range(n), key=lambda k: lengths[k])
    chain = []
    while end != -1:
        chain.append(entries[end])
        end = prev[end]
    chain.reverse()
    return chain
