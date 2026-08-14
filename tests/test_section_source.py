"""Canonical chapter text partitioning tests."""
from __future__ import annotations

import pytest

from pdf_learner import section_source


def _explicit_inventory() -> dict:
    return {
        "has_explicit_subchapters": True,
        "sections": [
            {
                "id": "section_1",
                "heading": "1.1 설치",
                "level": 1,
                "parent_id": None,
                "explicit_subchapter": True,
                "source_anchor": {"text": "1.1 설치", "occurrence": 2},
            },
            {
                "id": "section_2",
                "heading": "1.1.1 준비",
                "level": 2,
                "parent_id": "section_1",
                "explicit_subchapter": True,
                "source_anchor": {"text": "1.1.1 준비", "occurrence": 2},
            },
            {
                "id": "section_3",
                "heading": "1.2 실행",
                "level": 1,
                "parent_id": None,
                "explicit_subchapter": True,
                "source_anchor": {"text": "1.2 실행", "occurrence": 2},
            },
        ],
    }


def test_prepare_section_source_losslessly_partitions_repeated_headings():
    text = (
        "1장 시작\n1.1 설치\n1.1.1 준비\n1.2 실행\n\n"
        "서문 내용\n1.1 설치\n설치 본문\n"
        "1.1.1 준비\n준비 본문\n1.2 실행\n실행 본문"
    )

    prepared = section_source.prepare_section_source(
        text, _explicit_inventory(),
    )

    regions = prepared["structured_sections"]
    assert [region["kind"] for region in regions] == [
        "preamble", "section", "section", "section",
    ]
    assert regions[0]["source_text"].endswith("서문 내용\n")
    assert regions[1]["source_text"] == "1.1 설치\n설치 본문\n"
    assert regions[2]["source_text"] == "1.1.1 준비\n준비 본문\n"
    assert regions[3]["source_text"] == "1.2 실행\n실행 본문"
    assert "".join(region["source_text"] for region in regions) == text
    assert prepared["coverage"]["complete"] is True

    inventory = prepared["section_inventory"]
    assert inventory["source_binding"]["version"] == "canonical-char-span-v1"
    assert inventory["source_binding"]["source_char_count"] == len(text)
    assert len(inventory["source_binding"]["source_sha256"]) == 64
    assert inventory["sections"][1]["source_span"] == regions[2]["source_span"]
    assert "source_text" not in inventory["sections"][1]


def test_prepare_section_source_does_not_count_heading_prefix_as_exact_anchor():
    text = (
        "목차\n1.1 설치 안내\n\n"
        "1.1 설치\n설치 본문\n"
        "1.2 실행\n실행 본문"
    )
    inventory = {
        "has_explicit_subchapters": True,
        "sections": [{
            "id": "section_1",
            "heading": "1.1 설치",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
            "source_anchor": {"text": "1.1 설치", "occurrence": 1},
        }, {
            "id": "section_2",
            "heading": "1.2 실행",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
            "source_anchor": {"text": "1.2 실행", "occurrence": 1},
        }],
    }

    prepared = section_source.prepare_section_source(text, inventory)

    regions = prepared["structured_sections"]
    assert regions[0]["source_text"] == "목차\n1.1 설치 안내\n\n"
    assert regions[1]["source_text"] == "1.1 설치\n설치 본문\n"


def test_prepare_section_source_uses_whole_text_without_subchapters():
    text = "제목\n전체 챕터 본문"
    inventory = {
        "has_explicit_subchapters": False,
        "sections": [{
            "id": "section_1",
            "heading": "챕터 전체",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": False,
        }],
    }

    prepared = section_source.prepare_section_source(text, inventory)

    assert prepared["structured_sections"] == [{
        "kind": "chapter",
        "section_id": "section_1",
        "heading": "챕터 전체",
        "level": 1,
        "parent_id": None,
        "render_heading": False,
        "source_span": [0, len(text)],
        "source_text": text,
    }]


def test_detect_section_candidates_keeps_repeated_and_ocr_damaged_headings():
    text = (
        "소개\n"
        "1.1 MySQL 소개\n"
        "1.2 왜 MySQL인가?\n\n"
        "1.1 MySQL 소개\n"
        "본문\n"
        "].2 왜 MySQL안가?\n"
        "1. 최상위 절\n"
        "2) 다음 절\n"
        "3 마지막 절\n"
        "그림 1.1 서버 구조\n"
        "본문에서 1.2를 참고한다."
    )

    assert section_source.detect_section_candidates(text) == [
        {"text": "1.1 MySQL 소개", "occurrence": 1},
        {"text": "1.2 왜 MySQL인가?", "occurrence": 1},
        {"text": "1.1 MySQL 소개", "occurrence": 2},
        {"text": "].2 왜 MySQL안가?", "occurrence": 1},
        {"text": "1. 최상위 절", "occurrence": 1},
        {"text": "2) 다음 절", "occurrence": 1},
        {"text": "3 마지막 절", "occurrence": 1},
    ]


def test_candidate_audit_rejects_silent_whole_chapter_fallback():
    text = (
        "소개\n1. MySQL 소개\n본문\n2) 왜 MySQL인가?\n본문\n"
        "3 설치 절차\n본문"
    )
    inventory = {
        "has_explicit_subchapters": False,
        "sections": [{
            "id": "chapter_full",
            "heading": "챕터 전체",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": False,
        }],
        "candidate_exclusions": [],
    }

    with pytest.raises(section_source.SectionSourceValidationError) as exc:
        section_source.audit_section_inventory(text, inventory)

    assert "section_inventory.candidate_audit.unaccounted" in exc.value.invalid_fields
    assert exc.value.details["unaccounted_candidates"] == [
        {"text": "1. MySQL 소개", "occurrence": 1},
        {"text": "2) 왜 MySQL인가?", "occurrence": 1},
        {"text": "3 설치 절차", "occurrence": 1},
    ]


def test_candidate_audit_accepts_selected_body_and_excluded_toc_occurrences():
    text = (
        "소개\n1.1 설치\n1.2 실행\n\n"
        "1.1 설치\n설치 본문\n1.2 실행\n실행 본문"
    )
    inventory = {
        "has_explicit_subchapters": True,
        "sections": [{
            "id": "install",
            "heading": "1.1 설치",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
            "source_anchor": {"text": "1.1 설치", "occurrence": 2},
        }, {
            "id": "run",
            "heading": "1.2 실행",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
            "source_anchor": {"text": "1.2 실행", "occurrence": 2},
        }],
        "candidate_exclusions": [{
            "source_anchor": {"text": "1.1 설치", "occurrence": 1},
            "reason": "toc_fragment",
        }, {
            "source_anchor": {"text": "1.2 실행", "occurrence": 1},
            "reason": "toc_fragment",
        }],
    }

    audit = section_source.audit_section_inventory(text, inventory)

    assert audit["complete"] is True
    assert audit["review_required"] is True
    assert audit["unaccounted_candidates"] == []


def test_required_section_review_must_pass_without_findings():
    missing = section_source.invalid_section_review_fields(None, required=True)
    assert missing == ["section_review"]

    review = {
        "status": "passed",
        "reviewed_against": [
            "chapter_text", "section_inventory", "section_candidates",
        ],
        "missing_sections": [],
        "false_sections": [],
        "hierarchy_issues": [],
        "unresolved_candidates": [],
    }
    assert section_source.invalid_section_review_fields(review, required=True) == []

    review["missing_sections"] = ["1.2 실행"]
    assert "section_review.missing_sections" in (
        section_source.invalid_section_review_fields(review, required=True)
    )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda value: value["sections"][0].pop("source_anchor"),
            "section_inventory.sections[0].source_anchor",
        ),
        (
            lambda value: value["sections"][0]["source_anchor"].update(
                {"text": "없는 제목"}
            ),
            "section_inventory.sections[0].source_anchor.match",
        ),
        (
            lambda value: value["sections"][1]["source_anchor"].update(
                {"text": "1.1 설치", "occurrence": 2}
            ),
            "section_inventory.sections[1].source_anchor.order",
        ),
    ],
)
def test_prepare_section_source_rejects_unresolvable_partition(
    mutate, expected,
):
    inventory = _explicit_inventory()
    mutate(inventory)

    with pytest.raises(section_source.SectionSourceValidationError) as exc:
        section_source.prepare_section_source(
            "1.1 설치\n1.1.1 준비\n1.2 실행\n"
            "1.1 설치\n본문\n1.1.1 준비\n본문\n1.2 실행\n본문",
            inventory,
        )

    assert expected in exc.value.invalid_fields


def test_prepared_inventory_binding_detects_stale_or_gapped_source():
    text = "1.1 설치\n본문\n1.1.1 준비\n본문\n1.2 실행\n본문"
    inventory = _explicit_inventory()
    for section in inventory["sections"]:
        section["source_anchor"]["occurrence"] = 1
    prepared = section_source.prepare_section_source(text, inventory)
    bound_inventory = prepared["section_inventory"]

    assert section_source.invalid_source_binding_fields(
        bound_inventory, text,
    ) == []

    bound_inventory["source_binding"]["regions"][1]["source_span"][0] += 1
    assert "section_inventory.source_binding.regions" in (
        section_source.invalid_source_binding_fields(bound_inventory, text)
    )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda value: value.pop("sections"),
            "section_inventory.sections",
        ),
        (
            lambda value: value["source_binding"]["regions"][1].update(
                {"section_id": "section_3"}
            ),
            "section_inventory.source_binding.regions",
        ),
        (
            lambda value: value["source_binding"]["regions"][1].update(
                {"kind": "chapter"}
            ),
            "section_inventory.source_binding.regions",
        ),
    ],
)
def test_prepared_inventory_binding_rejects_inventory_or_region_tampering(
    mutate, expected,
):
    text = "1.1 설치\n본문\n1.1.1 준비\n본문\n1.2 실행\n본문"
    inventory = _explicit_inventory()
    for section in inventory["sections"]:
        section["source_anchor"]["occurrence"] = 1
    bound_inventory = section_source.prepare_section_source(
        text, inventory,
    )["section_inventory"]

    mutate(bound_inventory)

    assert expected in section_source.invalid_source_binding_fields(
        bound_inventory, text,
    )
