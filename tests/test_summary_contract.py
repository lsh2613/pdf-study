"""Section-guided summary completeness contract tests."""
from __future__ import annotations

from pdf_learner import summary_contract


def test_quality_example_passes_without_length_rule():
    data = {
        "summary": "짧아도 계약은 글자 수로 판단하지 않는다.",
        **summary_contract.quality_payload_example(),
    }

    assert summary_contract.missing_summary_quality_fields(data) == []


def test_quality_contract_requires_inventory_and_review():
    assert summary_contract.missing_summary_quality_fields({}) == [
        "section_inventory",
        "summary_review",
    ]


def test_quality_contract_does_not_revalidate_inventory_after_generation():
    data = {
        "summary": "제목을 Markdown heading으로 반복하지 않아도 저장 검증은 통과한다.",
        "section_inventory": {
            "has_explicit_subchapters": True,
            "sections": [{
                "id": "duplicate",
                "heading": "2.1 분석 단계에서만 사용하는 절",
                "level": 99,
                "parent_id": "missing-parent",
                "explicit_subchapter": True,
            }, {
                "id": "duplicate",
                "heading": "2.2 두 번째 절",
                "level": 1,
                "parent_id": None,
                "explicit_subchapter": False,
            }],
        },
        "summary_review": summary_contract.summary_review_example(),
    }
    chapter_text = "2.1 실제 제목\n본문\n2.1 실제 제목\n추가 본문"

    assert summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="02. 설치",
    ) == []


def test_quality_contract_requires_review_against_text_and_draft_only():
    data = summary_contract.quality_payload_example()
    data["summary_review"]["reviewed_against"] = [
        "chapter_text",
        "section_inventory",
        "draft_summary",
    ]

    assert summary_contract.missing_summary_quality_fields(data) == [
        "summary_review.reviewed_against",
    ]
    assert summary_contract.REVIEW_INPUTS == ("chapter_text", "draft_summary")


def test_quality_contract_ignores_legacy_section_reviews():
    data = summary_contract.quality_payload_example()
    data["summary_review"]["section_reviews"] = [{
        "section_id": "unknown",
        "status": "needs_revision",
        "missing_significant_content": ["이 필드는 더 이상 검증하지 않음"],
        "distortions": ["이 필드는 더 이상 검증하지 않음"],
    }]

    assert summary_contract.missing_summary_quality_fields(data) == []


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
        "draft_summary",
    ]
    assert "covered_section_ids" not in normalized["summary_review"]
    assert "covered_point_ids" not in normalized["summary_review"]


def test_review_normalization_preserves_unknown_inputs_for_validation():
    for unknown in ("unexpected_source", 123):
        data = summary_contract.quality_payload_example()
        data["summary_review"]["reviewed_against"].append(unknown)

        normalized = summary_contract.normalize_summary_quality_payload(data)

        assert unknown in normalized["summary_review"]["reviewed_against"]
        assert any(
            field.startswith("summary_review.reviewed_against")
            for field in summary_contract.missing_summary_quality_fields(
                normalized
            )
        )


def test_canonical_inventory_is_sanitized_without_points():
    data = summary_contract.quality_payload_example()
    data["section_inventory"]["sections"][0]["important_points"] = [{
        "id": "point_1",
        "content": "더는 canonical 구조 데이터가 아닌 값",
    }]

    normalized = summary_contract.normalize_summary_quality_payload(data)

    assert "important_points" not in normalized["section_inventory"]["sections"][0]
