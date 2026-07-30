"""Semantic summary completeness contract tests."""
from __future__ import annotations

from pdf_learner import summary_contract


def test_quality_example_passes_without_length_rule():
    data = {
        "summary": "짧아도 계약은 글자 수로 판단하지 않는다.",
        **summary_contract.quality_payload_example(),
    }

    assert summary_contract.missing_summary_quality_fields(data) == []


def test_quality_contract_requires_content_map_and_review():
    assert summary_contract.missing_summary_quality_fields({}) == [
        "content_map",
        "summary_review",
    ]


def test_quality_contract_requires_single_whole_chapter_unit_without_subchapters():
    data = summary_contract.quality_payload_example()
    second = {
        **data["content_map"]["sections"][0],
        "id": "section_2",
        "important_points": [{
            "id": "point_2",
            "content": "두 번째 내용",
            "significance": "중요한 이유",
        }],
    }
    data["content_map"]["sections"].append(second)
    data["summary_review"]["covered_section_ids"].append("section_2")
    data["summary_review"]["covered_point_ids"].append("point_2")

    assert summary_contract.missing_summary_quality_fields(data) == [
        "content_map.sections",
    ]


def test_quality_contract_accepts_all_explicit_subchapters_when_reviewed():
    data = summary_contract.quality_payload_example()
    data["summary"] = "## 1.1 첫 절\n내용\n\n## 1.2 둘째 절\n내용"
    content_map = data["content_map"]
    content_map["has_explicit_subchapters"] = True
    content_map["sections"][0]["heading"] = "1.1 첫 절"
    content_map["sections"][0]["explicit_subchapter"] = True
    content_map["sections"].append({
        "id": "section_2",
        "heading": "1.2 둘째 절",
        "explicit_subchapter": True,
        "important_points": [{
            "id": "point_2",
            "content": "둘째 절 핵심",
            "significance": "둘째 절의 결론",
        }],
    })
    review = data["summary_review"]
    review["covered_section_ids"].append("section_2")
    review["covered_point_ids"].append("point_2")

    assert summary_contract.missing_summary_quality_fields(data) == []


def test_quality_contract_rejects_explicit_subchapter_missing_from_summary():
    data = summary_contract.quality_payload_example()
    data["summary"] = "## 1.1 첫 절\n첫 절만 요약했다."
    content_map = data["content_map"]
    content_map["has_explicit_subchapters"] = True
    content_map["sections"][0].update(
        heading="1.1 첫 절",
        explicit_subchapter=True,
    )
    content_map["sections"].append({
        "id": "section_2",
        "heading": "1.2 둘째 절",
        "explicit_subchapter": True,
        "important_points": [{
            "id": "point_2",
            "content": "둘째 절 핵심",
            "significance": "둘째 절의 결론",
        }],
    })
    review = data["summary_review"]
    review["covered_section_ids"].append("section_2")
    review["covered_point_ids"].append("point_2")

    assert "content_map.sections[1].heading" in (
        summary_contract.missing_summary_quality_fields(data)
    )


def test_quality_contract_rejects_uncovered_content():
    data = summary_contract.quality_payload_example()
    data["summary_review"]["covered_point_ids"] = []

    missing = summary_contract.missing_summary_quality_fields(data)

    assert "summary_review.covered_point_ids" in missing


def test_quality_contract_rejects_known_omission_or_distortion():
    data = summary_contract.quality_payload_example()
    data["summary_review"]["missing_significant_content"] = ["예외 조건 누락"]
    data["summary_review"]["distortions"] = ["인과관계가 반대로 설명됨"]

    missing = summary_contract.missing_summary_quality_fields(data)

    assert "summary_review.missing_significant_content" in missing
    assert "summary_review.distortions" in missing


def test_quality_contract_rejects_needs_revision():
    data = summary_contract.quality_payload_example()
    data["summary_review"]["status"] = "needs_revision"

    assert "summary_review.status" in (
        summary_contract.missing_summary_quality_fields(data)
    )
