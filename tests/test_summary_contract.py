"""Section-first summary completeness contract tests."""
from __future__ import annotations

from pdf_learner import summary_contract


def test_quality_example_passes_without_length_rule():
    data = {
        "summary": "짧아도 계약은 글자 수로 판단하지 않는다.",
        **summary_contract.quality_payload_example(),
    }

    assert summary_contract.missing_summary_quality_fields(data) == []


def test_quality_contract_requires_section_inventory_and_review():
    assert summary_contract.missing_summary_quality_fields({}) == [
        "section_inventory",
        "summary_review",
    ]


def test_quality_contract_requires_single_whole_chapter_when_no_subchapters():
    data = summary_contract.quality_payload_example()
    data["section_inventory"]["sections"].append({
        "id": "section_2",
        "heading": "임의로 만든 절",
        "level": 1,
        "parent_id": None,
        "explicit_subchapter": False,
    })
    data["summary_review"]["section_reviews"].append({
        "section_id": "section_2",
        "status": "passed",
        "missing_significant_content": [],
        "distortions": [],
    })

    assert summary_contract.missing_summary_quality_fields(data) == [
        "section_inventory.sections",
    ]


def test_quality_contract_accepts_nested_explicit_subchapters_when_reviewed():
    data = summary_contract.quality_payload_example()
    data["summary"] = (
        "## 설치\n내용\n\n### 패키지 설치\n내용\n\n#### RPM 설치\n내용"
    )
    data["section_inventory"] = {
        "has_explicit_subchapters": True,
        "sections": [
            {
                "id": "section_1",
                "heading": "설치",
                "level": 1,
                "parent_id": None,
                "explicit_subchapter": True,
            },
            {
                "id": "section_2",
                "heading": "패키지 설치",
                "level": 2,
                "parent_id": "section_1",
                "explicit_subchapter": True,
            },
            {
                "id": "section_3",
                "heading": "RPM 설치",
                "level": 3,
                "parent_id": "section_2",
                "explicit_subchapter": True,
            },
        ],
    }
    data["summary_review"]["section_reviews"] = [
        {
            "section_id": f"section_{index}",
            "status": "passed",
            "missing_significant_content": [],
            "distortions": [],
        }
        for index in range(1, 4)
    ]

    assert summary_contract.missing_summary_quality_fields(data) == []


def test_quality_contract_rejects_invalid_parent_or_level():
    data = summary_contract.quality_payload_example()
    data["section_inventory"] = {
        "has_explicit_subchapters": True,
        "sections": [
            {
                "id": "section_1",
                "heading": "첫 절",
                "level": 1,
                "parent_id": None,
                "explicit_subchapter": True,
            },
            {
                "id": "section_2",
                "heading": "잘못된 하위 절",
                "level": 1,
                "parent_id": "section_1",
                "explicit_subchapter": True,
            },
        ],
    }
    data["summary"] = "## 첫 절\n내용\n\n## 잘못된 하위 절\n내용"
    data["summary_review"]["section_reviews"] = [
        {
            "section_id": section_id,
            "status": "passed",
            "missing_significant_content": [],
            "distortions": [],
        }
        for section_id in ("section_1", "section_2")
    ]

    missing = summary_contract.missing_summary_quality_fields(data)

    assert "section_inventory.sections[1].level" in missing


def test_quality_contract_rejects_explicit_subchapter_missing_from_summary():
    data = summary_contract.quality_payload_example()
    data["summary"] = "## 첫 절\n첫 절만 요약했다."
    data["section_inventory"] = {
        "has_explicit_subchapters": True,
        "sections": [
            {
                "id": "section_1",
                "heading": "첫 절",
                "level": 1,
                "parent_id": None,
                "explicit_subchapter": True,
            },
            {
                "id": "section_2",
                "heading": "둘째 절",
                "level": 1,
                "parent_id": None,
                "explicit_subchapter": True,
            },
        ],
    }
    data["summary_review"]["section_reviews"] = [
        {
            "section_id": section_id,
            "status": "passed",
            "missing_significant_content": [],
            "distortions": [],
        }
        for section_id in ("section_1", "section_2")
    ]

    assert "section_inventory.sections[1].heading" in (
        summary_contract.missing_summary_quality_fields(data)
    )


def test_quality_contract_rejects_missing_or_duplicate_section_review():
    data = summary_contract.quality_payload_example()
    review = data["summary_review"]["section_reviews"][0]
    data["summary_review"]["section_reviews"] = [review, dict(review)]

    assert "summary_review.section_reviews" in (
        summary_contract.missing_summary_quality_fields(data)
    )


def test_quality_contract_rejects_known_section_omission_or_distortion():
    data = summary_contract.quality_payload_example()
    section_review = data["summary_review"]["section_reviews"][0]
    section_review["status"] = "needs_revision"
    section_review["missing_significant_content"] = ["예외 조건 누락"]
    section_review["distortions"] = ["인과관계가 반대로 설명됨"]

    missing = summary_contract.missing_summary_quality_fields(data)

    assert "summary_review.section_reviews[0].status" in missing
    assert (
        "summary_review.section_reviews[0].missing_significant_content" in missing
    )
    assert "summary_review.section_reviews[0].distortions" in missing


def test_quality_contract_rejects_known_chapter_omission_or_distortion():
    data = summary_contract.quality_payload_example()
    data["summary_review"]["missing_significant_content"] = ["절 간 관계 누락"]
    data["summary_review"]["distortions"] = ["전체 결론 왜곡"]

    missing = summary_contract.missing_summary_quality_fields(data)

    assert "summary_review.missing_significant_content" in missing
    assert "summary_review.distortions" in missing


def test_quality_contract_rejects_needs_revision():
    data = summary_contract.quality_payload_example()
    data["summary_review"]["status"] = "needs_revision"

    assert "summary_review.status" in (
        summary_contract.missing_summary_quality_fields(data)
    )


def test_legacy_content_map_is_normalized_without_points():
    data = {
        "summary": "## 첫 절\n내용",
        "content_map": {
            "has_explicit_subchapters": True,
            "sections": [{
                "id": "section_1",
                "heading": "첫 절",
                "explicit_subchapter": True,
                "important_points": [{
                    "id": "point_1",
                    "content": "과거 핵심 내용",
                    "significance": "과거 중요 이유",
                }],
            }],
        },
        "summary_review": {
            "status": "passed",
            "reviewed_against": ["chapter_text", "content_map", "draft_summary"],
            "covered_section_ids": ["section_1"],
            "covered_point_ids": ["point_1"],
            "missing_significant_content": [],
            "distortions": [],
        },
    }

    normalized = summary_contract.normalize_summary_quality_payload(data)

    assert "content_map" not in normalized
    assert "important_points" not in normalized["section_inventory"]["sections"][0]
    assert normalized["summary_review"]["reviewed_against"] == [
        "chapter_text",
        "section_inventory",
        "draft_summary",
    ]
    assert normalized["summary_review"]["section_reviews"] == [{
        "section_id": "section_1",
        "status": "passed",
        "missing_significant_content": [],
        "distortions": [],
    }]


def test_canonical_inventory_is_sanitized_without_points():
    data = summary_contract.quality_payload_example()
    data["section_inventory"]["sections"][0]["important_points"] = [{
        "id": "point_1",
        "content": "더는 canonical 구조 데이터가 아닌 값",
    }]

    normalized = summary_contract.normalize_summary_quality_payload(data)

    assert "important_points" not in normalized["section_inventory"]["sections"][0]


def test_legacy_mixed_map_keeps_only_explicit_subchapter_structure():
    data = {
        "content_map": {
            "has_explicit_subchapters": True,
            "sections": [
                {
                    "id": "whole_chapter",
                    "heading": "챕터 전체",
                    "explicit_subchapter": False,
                    "important_points": [{"id": "p1"}],
                },
                {
                    "id": "section_1",
                    "heading": "실제 소제목",
                    "explicit_subchapter": True,
                    "important_points": [{"id": "p2"}],
                },
            ],
        },
        "summary_review": {
            "status": "passed",
            "reviewed_against": ["chapter_text", "content_map", "draft_summary"],
            "covered_section_ids": ["whole_chapter", "section_1"],
            "covered_point_ids": ["p1", "p2"],
            "missing_significant_content": [],
            "distortions": [],
        },
    }

    normalized = summary_contract.normalize_summary_quality_payload(data)

    assert [
        section["id"] for section in normalized["section_inventory"]["sections"]
    ] == ["section_1"]
    assert [
        review["section_id"] for review in normalized["summary_review"]["section_reviews"]
    ] == ["section_1"]
