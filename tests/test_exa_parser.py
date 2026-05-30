"""exa_client._parse_exa_plaintext 단위 테스트.

실제 Exa MCP 호출은 외부 의존이라 여기서 다루지 않는다 — 파서만 검증.
"""
from __future__ import annotations

from pdf_study import exa_client


def test_parse_single_block():
    raw = (
        "Title: Extract Function - Refactoring\n"
        "URL: https://refactoring.com/catalog/extractFunction.html\n"
        "Published: 2024-01-01\n"
        "Author: Martin Fowler\n"
        "Highlights:\n"
        "Extract a fragment of code into a new function whose name\n"
        "explains the purpose of the function."
    )
    out = exa_client._parse_exa_plaintext(raw)
    assert len(out) == 1
    item = out[0]
    assert item["title"].startswith("Extract Function")
    assert item["url"] == "https://refactoring.com/catalog/extractFunction.html"
    assert "explains the purpose" in item["snippet"]


def test_parse_multiple_blocks():
    raw = (
        "Title: A\n"
        "URL: https://a.example/\n"
        "Highlights:\n"
        "snippet A\n"
        "Title: B\n"
        "URL: https://b.example/\n"
        "Highlights:\n"
        "snippet B"
    )
    out = exa_client._parse_exa_plaintext(raw)
    assert [x["title"] for x in out] == ["A", "B"]
    assert [x["url"] for x in out] == ["https://a.example/", "https://b.example/"]


def test_parse_empty_returns_empty_list():
    assert exa_client._parse_exa_plaintext("") == []


def test_normalize_skips_results_without_url():
    """_normalize는 url이 없는 항목을 거른다."""
    out = exa_client._normalize([
        {"title": "no url", "snippet": "x"},
        {"title": "with url", "url": "https://e.com/", "snippet": "y"},
    ])
    assert len(out) == 1
    assert out[0]["url"] == "https://e.com/"


def test_normalize_limits_to_max_results():
    items = [{"title": f"t{i}", "url": f"https://e{i}.com/"} for i in range(20)]
    out = exa_client._normalize(items)
    assert len(out) == exa_client.MAX_RESULTS
