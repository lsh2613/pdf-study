"""Section-first summary completeness contract tests."""
from __future__ import annotations

from pdf_learner import summary_contract


def _reviewed_sections(
    sections: list[dict[str, object]],
    summary: str,
) -> dict[str, object]:
    return {
        "summary": summary,
        "section_inventory": {
            "has_explicit_subchapters": True,
            "sections": sections,
        },
        "summary_review": {
            "status": "passed",
            "reviewed_against": list(summary_contract.REVIEW_INPUTS),
            "section_reviews": [
                {
                    "section_id": section["id"],
                    "status": "passed",
                    "missing_significant_content": [],
                    "distortions": [],
                }
                for section in sections
            ],
            "missing_significant_content": [],
            "distortions": [],
        },
    }


def test_quality_example_passes_without_length_rule():
    data = {
        "summary": "짧아도 계약은 글자 수로 판단하지 않는다.",
        **summary_contract.quality_payload_example(),
    }

    assert summary_contract.missing_summary_quality_fields(data) == []


def test_quality_contract_rejects_inventory_that_omits_confident_source_hierarchy():
    data = {
        "summary": "설치와 설정을 설명한다.",
        **summary_contract.quality_payload_example(),
    }
    chapter_text = """\
2.1 MySQL 서버 설치
2.2 MySQL 서버의 시작과 종료
2.1 MySQL 서버 설치
본문 설명
2.1.1 버전과 에디션 선택
세부 설명
2.2 MySQL 서버의 시작과 종료
종료 절차 설명
"""

    missing = summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="02. 설치와 설정",
    )

    assert "section_inventory.source_headings[2.1]" in missing
    assert "section_inventory.source_headings[2.1.1]" in missing
    assert "section_inventory.source_headings[2.2]" in missing


def test_source_heading_one_inventory_section_cannot_cover_two_headings():
    data = {
        "summary": "## 2.2 설치 준비\n준비 내용을 설명한다.",
        "section_inventory": {
            "has_explicit_subchapters": True,
            "sections": [{
                "id": "setup",
                "heading": "2.2 설치 준비",
                "level": 1,
                "parent_id": None,
                "explicit_subchapter": True,
            }],
        },
        "summary_review": {
            "status": "passed",
            "reviewed_against": list(summary_contract.REVIEW_INPUTS),
            "section_reviews": [{
                "section_id": "setup",
                "status": "passed",
                "missing_significant_content": [],
                "distortions": [],
            }],
            "missing_significant_content": [],
            "distortions": [],
        },
    }
    chapter_text = """\
2.1 설치
2.2 설치 준비
2.1 설치
본문
2.2 설치 준비
본문
"""

    missing = summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="02. 설치와 설정",
    )

    assert "section_inventory.source_headings[2.1]" in missing
    assert "section_inventory.source_headings[2.2]" not in missing


def test_quality_contract_accepts_every_confident_source_heading_at_its_depth():
    data = {
        "summary": (
            "## 2.1 MySQL 서버 설치\n설치 설명\n\n"
            "### 2.1.1 버전과 에디션 선택\n선택 설명\n\n"
            "## 2.2 MySQL 서버의 시작과 종료\n시작과 종료 설명"
        ),
        "section_inventory": {
            "has_explicit_subchapters": True,
            "sections": [
                {
                    "id": "install",
                    "heading": "2.1 MySQL 서버 설치",
                    "level": 1,
                    "parent_id": None,
                    "explicit_subchapter": True,
                },
                {
                    "id": "version",
                    "heading": "2.1.1 버전과 에디션 선택",
                    "level": 2,
                    "parent_id": "install",
                    "explicit_subchapter": True,
                },
                {
                    "id": "lifecycle",
                    "heading": "2.2 MySQL 서버의 시작과 종료",
                    "level": 1,
                    "parent_id": None,
                    "explicit_subchapter": True,
                },
            ],
        },
        "summary_review": {
            "status": "passed",
            "reviewed_against": list(summary_contract.REVIEW_INPUTS),
            "section_reviews": [
                {
                    "section_id": section_id,
                    "status": "passed",
                    "missing_significant_content": [],
                    "distortions": [],
                }
                for section_id in ("install", "version", "lifecycle")
            ],
            "missing_significant_content": [],
            "distortions": [],
        },
    }
    chapter_text = """\
2.1 MySQL 서버 설치
2.2 MySQL 서버의 시작과 종료
2.1 MySQL 서버 설치
본문 설명
2.1.1 버전과 에디션 선택
세부 설명
2.2 MySQL 서버의 시작과 종료
종료 절차 설명
"""

    assert summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="02. 설치와 설정",
    ) == []


def test_quality_contract_does_not_treat_book_toc_as_front_matter_sections():
    data = {
        "summary": "## 책 사용 설명서\n도서 홈페이지와 예제 파일을 안내한다.",
        **summary_contract.quality_payload_example(),
    }
    chapter_text = """\
책 사용 설명서
8.1 트랜잭션
8.2 인덱스
8.1 트랜잭션
다른 장의 목차 조각
"""

    assert summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="서문·예제 데이터베이스",
    ) == []


def test_quality_contract_allows_ocr_number_correction_when_heading_matches():
    data = {
        "summary": (
            "## 3.2 사용자 계정 관리\n계정 관리 설명\n\n"
            "### 3.2.2 계정 생성\n계정 생성 설명\n\n"
            "#### 3.2.2.7 ACCOUNT LOCK / UNLOCK\n잠금 설명"
        ),
        "section_inventory": {
            "has_explicit_subchapters": True,
            "sections": [
                {
                    "id": "management",
                    "heading": "3.2 사용자 계정 관리",
                    "level": 1,
                    "parent_id": None,
                    "explicit_subchapter": True,
                },
                {
                    "id": "account",
                    "heading": "3.2.2 계정 생성",
                    "level": 2,
                    "parent_id": "management",
                    "explicit_subchapter": True,
                },
                {
                    "id": "lock",
                    "heading": "3.2.2.7 ACCOUNT LOCK / UNLOCK",
                    "level": 3,
                    "parent_id": "account",
                    "explicit_subchapter": True,
                },
            ],
        },
        "summary_review": {
            "status": "passed",
            "reviewed_against": list(summary_contract.REVIEW_INPUTS),
            "section_reviews": [
                {
                    "section_id": section_id,
                    "status": "passed",
                    "missing_significant_content": [],
                    "distortions": [],
                }
                for section_id in ("management", "account", "lock")
            ],
            "missing_significant_content": [],
            "distortions": [],
        },
    }
    chapter_text = """\
3.2 사용자 계정 관리
3.2 사용자 계정 관리
3.2.2 계정 생성
본문
3.2.2 계정 생성
3.2.27 ACCOUNT LOCK / UNLOCK
"""

    assert summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="03. 사용자 및 권한",
    ) == []


def test_quality_contract_rejects_same_number_with_unrelated_title():
    data = _reviewed_sections([
        {
            "id": "invented",
            "heading": "2.1 임의로 만든 제목",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
        },
    ], "## 2.1 임의로 만든 제목\n요약")
    chapter_text = "2.1 실제 설치\n본문\n2.1 실제 설치\n추가 본문"

    missing = summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="02. 설치",
    )

    assert "section_inventory.source_headings[2.1]" in missing


def test_quality_contract_rejects_descendant_number_prefix_match():
    data = _reviewed_sections([
        {
            "id": "descendant",
            "heading": "2.1.9 실제 설치",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
        },
    ], "## 2.1.9 실제 설치\n요약")
    chapter_text = "2.1 실제 설치\n본문\n2.1 실제 설치\n추가 본문"

    missing = summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="02. 설치",
    )

    assert "section_inventory.source_headings[2.1]" in missing


def test_quality_contract_validates_title_fallback_level_and_parent():
    sections = [
        {
            "id": "management",
            "heading": "3.2 사용자 계정 관리",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
        },
        {
            "id": "account",
            "heading": "3.2.2 계정 생성",
            "level": 2,
            "parent_id": "management",
            "explicit_subchapter": True,
        },
        {
            "id": "lock",
            "heading": "3.2.2.7 ACCOUNT LOCK / UNLOCK",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
        },
    ]
    data = _reviewed_sections(
        sections,
        "\n".join(f"## {section['heading']}\n요약" for section in sections),
    )
    chapter_text = """\
3.2 사용자 계정 관리
3.2 사용자 계정 관리
3.2.2 계정 생성
본문
3.2.2 계정 생성
3.2.27 ACCOUNT LOCK / UNLOCK
"""

    missing = summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="03. 사용자 및 권한",
    )

    assert "section_inventory.source_headings[3.2.27].level" in missing
    assert "section_inventory.source_headings[3.2.27].parent_id" in missing


def test_quality_contract_rejects_reversed_source_section_order():
    sections = [
        {
            "id": "first",
            "heading": "2.1 첫째",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
        },
        {
            "id": "second",
            "heading": "2.2 둘째",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
        },
    ]
    data = _reviewed_sections(sections, "## 2.1 첫째\n요약\n## 2.2 둘째\n요약")
    chapter_text = "2.2 둘째\n본문\n2.1 첫째\n본문\n2.2 둘째\n2.1 첫째"

    missing = summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="02. 순서",
    )

    assert "section_inventory.source_headings.order" in missing


def test_quality_contract_does_not_promote_numbered_body_sentences():
    data = {
        "summary": "설치 절차를 설명한다.",
        **summary_contract.quality_payload_example(),
    }
    chapter_text = """\
설치 절차는 다음과 같다.
2.1 패키지를 내려받는다.
2.2 서비스를 실행한다.
이후 연결을 확인한다.
"""

    assert summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="02. 설치",
    ) == []


def test_quality_contract_requires_single_heading_at_chapter_start():
    data = {
        "summary": "한 문장만 쓴다.",
        **summary_contract.quality_payload_example(),
    }
    chapter_text = "1.1 MySQL 소개\n긴 본문 설명"

    missing = summary_contract.missing_summary_quality_fields(
        data,
        chapter_text=chapter_text,
        chapter_title="01. 소개",
    )

    assert "section_inventory.source_headings[1.1]" in missing


def test_quality_contract_requires_each_inventory_heading_in_summary():
    sections = [
        {
            "id": "install",
            "heading": "설치",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
        },
        {
            "id": "prepare",
            "heading": "설치 준비",
            "level": 1,
            "parent_id": None,
            "explicit_subchapter": True,
        },
    ]
    data = _reviewed_sections(sections, "## 설치 준비\n준비만 설명한다.")

    missing = summary_contract.missing_summary_quality_fields(data)

    assert "section_inventory.sections[0].heading" in missing
    assert "section_inventory.sections[1].heading" not in missing


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
