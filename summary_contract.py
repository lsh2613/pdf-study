"""Section-first summary structure and review validation.

The generation workflow inventories source structure before writing prose, then
reviews the full source text against each inventory section.  The inventory is
structure-only: it deliberately does not preselect important points that could
become an accidental ceiling on the resulting study summary.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any


EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
REVIEW_INPUTS = ("chapter_text", "section_inventory", "draft_summary")
NUMBERED_HEADING_PATTERN = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+)+)\s+(?P<heading>\S.*?)\s*$",
)


def quality_payload_example() -> dict[str, Any]:
    """Return a fresh, minimal passing section inventory and review payload."""
    return {
        "section_inventory": {
            "has_explicit_subchapters": False,
            "sections": [{
                "id": "section_1",
                "heading": "챕터 전체",
                "level": 1,
                "parent_id": None,
                "explicit_subchapter": False,
            }],
        },
        "summary_review": {
            "status": "passed",
            "reviewed_against": list(REVIEW_INPUTS),
            "section_reviews": [{
                "section_id": "section_1",
                "status": "passed",
                "missing_significant_content": [],
                "distortions": [],
            }],
            "missing_significant_content": [],
            "distortions": [],
        },
    }


def section_inventory_example() -> dict[str, Any]:
    return quality_payload_example()["section_inventory"]


def content_map_example() -> dict[str, Any]:
    """Compatibility alias for clients importing the former example helper."""
    return section_inventory_example()


def summary_review_example() -> dict[str, Any]:
    return quality_payload_example()["summary_review"]


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_evidence_id(value: Any) -> bool:
    return (
        _is_nonempty_str(value)
        and EVIDENCE_ID_PATTERN.fullmatch(value) is not None
    )


def _normalize_visible_text(value: str) -> str:
    """Normalize Markdown decoration and whitespace for heading presence checks."""
    without_markdown = re.sub(r"[#*_`~]+", " ", value)
    return " ".join(without_markdown.casefold().split())


def _markdown_heading_texts(value: str) -> list[str]:
    headings: list[str] = []
    for line in value.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append(_normalize_visible_text(match.group(1)))
    return headings


def _chapter_number(chapter_title: str | None) -> str | None:
    if not isinstance(chapter_title, str):
        return None
    match = re.match(r"^\s*0*(\d+)\s*(?:[.:：]|\s)", chapter_title)
    return str(int(match.group(1))) if match else None


def _confident_numbered_source_headings(
    chapter_text: str,
    chapter_title: str | None,
) -> list[tuple[str, int, str]]:
    """Return conservative numbered headings that can be checked locally.

    Numberless and irregular headings remain the inventory model's job. This
    guard only recognizes repeated headings, a title-like first source line,
    contiguous mini-TOCs, and their descendants. It validates study-summary
    structure; it never chooses PDF chapter boundaries.
    """
    source_lines = chapter_text.splitlines()
    first_nonempty_line = next(
        (index for index, line in enumerate(source_lines) if line.strip()),
        None,
    )
    candidates: list[tuple[int, str, str]] = []
    for line_index, line in enumerate(source_lines):
        if len(line.strip()) > 140:
            continue
        match = NUMBERED_HEADING_PATTERN.fullmatch(line)
        if match:
            candidates.append((
                line_index,
                match.group("number"),
                match.group("heading"),
            ))
    if not candidates:
        return []

    expected_root = _chapter_number(chapter_title)
    if expected_root is None:
        # 서문에 반복된 책 전체 목차가 섞인 경우가 많다. 현재 챕터 번호를 제목에서
        # 확정할 수 없으면 다른 챕터의 목차 조각을 강제 section으로 오인하지 않는다.
        return []
    candidates = [
        item for item in candidates if item[1].split(".", 1)[0] == expected_root
    ]
    if not candidates:
        return []

    occurrences = Counter(number for _, number, _ in candidates)
    confident = {number for number, count in occurrences.items() if count >= 2}

    run: list[str] = []
    previous_line: int | None = None
    for line_index, number, heading in candidates:
        toc_like_title = re.search(r"[.!?。！？]\s*$", heading) is None
        if toc_like_title and (
            previous_line is None or line_index == previous_line + 1
        ):
            run.append(number)
        else:
            if len(run) >= 2:
                confident.update(run)
            run = [number] if toc_like_title else []
        previous_line = line_index if toc_like_title else None
    if len(run) >= 2:
        confident.update(run)

    first_candidate = candidates[0]
    if (
        not confident
        and first_candidate[0] == first_nonempty_line
        and re.search(r"[.!?。！？]\s*$", first_candidate[2]) is None
    ):
        confident.add(first_candidate[1])

    candidate_numbers = {number for _, number, _ in candidates}
    for number in sorted(
        candidate_numbers - confident,
        key=lambda value: (
            value.count("."),
            tuple(int(part) for part in value.split(".")),
        ),
    ):
        if number.rsplit(".", 1)[0] in confident:
            confident.add(number)

    title_by_number: dict[str, str] = {}
    last_index_by_number: dict[str, int] = {}
    for line_index, number, heading in candidates:
        title_by_number.setdefault(number, heading)
        last_index_by_number[number] = line_index
    return [
        (number, number.count("."), title_by_number[number])
        for number in sorted(
            confident,
            key=lambda value: last_index_by_number[value],
        )
    ]


def _validate_source_heading_coverage(
    inventory: dict[str, Any],
    chapter_text: str,
    chapter_title: str | None,
    missing: list[str],
) -> None:
    expected = _confident_numbered_source_headings(chapter_text, chapter_title)
    if not expected:
        return
    sections = inventory.get("sections")
    if not isinstance(sections, list):
        return

    indexed_sections = [
        (index, section)
        for index, section in enumerate(sections)
        if isinstance(section, dict)
    ]
    used_section_indexes: set[int] = set()
    matched: dict[str, dict[str, Any]] = {}
    resolved_numbers: dict[str, str] = {}
    matched_indexes: list[int] = []
    for number, _, source_heading in expected:
        normalized_source_heading = _normalize_visible_text(source_heading)
        number_candidates = [
            (index, section)
            for index, section in indexed_sections
            if index not in used_section_indexes
            and isinstance(section.get("heading"), str)
            and (
                heading_match := NUMBERED_HEADING_PATTERN.fullmatch(
                    section["heading"],
                )
            ) is not None
            and heading_match.group("number") == number
            and _normalize_visible_text(heading_match.group("heading"))
            == normalized_source_heading
        ]
        selected: tuple[int, dict[str, Any]] | None = None
        resolved_number = number
        if number_candidates:
            selected = number_candidates[0]
        else:
            title_candidates: list[tuple[int, dict[str, Any], str]] = []
            for index, section in indexed_sections:
                heading = section.get("heading")
                if index in used_section_indexes or not isinstance(heading, str):
                    continue
                heading_match = NUMBERED_HEADING_PATTERN.fullmatch(heading)
                if (
                    heading_match
                    and heading_match.group("number").replace(".", "")
                    != number.replace(".", "")
                ):
                    continue
                heading_title = (
                    heading_match.group("heading") if heading_match else heading
                )
                if _normalize_visible_text(heading_title) == normalized_source_heading:
                    title_candidates.append((
                        index,
                        section,
                        heading_match.group("number") if heading_match else number,
                    ))
            if len(title_candidates) == 1:
                section_index, section, resolved_number = title_candidates[0]
                selected = (section_index, section)

        if selected is None:
            missing.append(f"section_inventory.source_headings[{number}]")
            continue
        section_index, section = selected
        used_section_indexes.add(section_index)
        matched[number] = section
        resolved_numbers[number] = resolved_number
        matched_indexes.append(section_index)
        if section.get("level") != resolved_number.count("."):
            missing.append(
                f"section_inventory.source_headings[{number}].level"
            )

    if any(
        current <= previous
        for previous, current in zip(matched_indexes, matched_indexes[1:])
    ):
        missing.append("section_inventory.source_headings.order")

    for number, _, _ in expected:
        resolved_number = resolved_numbers.get(number)
        if resolved_number is None or resolved_number.count(".") <= 1:
            continue
        resolved_parent = resolved_number.rsplit(".", 1)[0]
        parent_number = next(
            (
                source_number
                for source_number, candidate_number in resolved_numbers.items()
                if candidate_number == resolved_parent
            ),
            None,
        )
        parent = matched.get(parent_number) if parent_number is not None else None
        if parent is not None and matched[number].get("parent_id") != parent.get("id"):
            missing.append(
                f"section_inventory.source_headings[{number}].parent_id"
            )


def _validate_string_list(
    value: Any,
    path: str,
    missing: list[str],
    *,
    allow_empty: bool,
) -> list[str] | None:
    if not isinstance(value, list):
        missing.append(path)
        return None
    if not allow_empty and not value:
        missing.append(path)
        return None
    normalized: list[str] = []
    valid = True
    for index, item in enumerate(value):
        if not _is_nonempty_str(item):
            missing.append(f"{path}[{index}]")
            valid = False
        else:
            normalized.append(item)
    if len(normalized) != len(set(normalized)):
        missing.append(path)
        valid = False
    return normalized if valid else None


def _validate_empty_review_findings(
    review: dict[str, Any],
    path: str,
    missing: list[str],
) -> None:
    for field in ("missing_significant_content", "distortions"):
        field_path = f"{path}.{field}" if path else field
        findings = _validate_string_list(
            review.get(field), field_path, missing, allow_empty=True,
        )
        if findings:
            missing.append(field_path)


def _legacy_section_inventory(content_map: dict[str, Any]) -> dict[str, Any]:
    sections: list[Any] = []
    raw_sections = content_map.get("sections")
    has_subchapters = content_map.get("has_explicit_subchapters")
    if isinstance(raw_sections, list):
        for section in raw_sections:
            if not isinstance(section, dict):
                sections.append(section)
                continue
            if (
                has_subchapters is True
                and section.get("explicit_subchapter") is not True
            ):
                continue
            sections.append({
                "id": section.get("id"),
                "heading": section.get("heading"),
                "level": section.get("level", 1),
                "parent_id": section.get("parent_id"),
                "explicit_subchapter": section.get("explicit_subchapter"),
            })
    return {
        "has_explicit_subchapters": has_subchapters,
        "sections": sections,
    }


def _sanitize_section_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(inventory)
    sections = inventory.get("sections")
    if isinstance(sections, list):
        normalized["sections"] = [
            {
                key: value
                for key, value in section.items()
                if key != "important_points"
            }
            if isinstance(section, dict) else section
            for section in sections
        ]
    return normalized


def _normalize_legacy_review(
    review: dict[str, Any],
    section_inventory: Any,
) -> dict[str, Any]:
    normalized = dict(review)
    reviewed_against = normalized.get("reviewed_against")
    if isinstance(reviewed_against, list):
        normalized["reviewed_against"] = [
            "section_inventory" if value == "content_map" else value
            for value in reviewed_against
        ]

    if "section_reviews" not in normalized:
        covered = normalized.get("covered_section_ids")
        if isinstance(covered, list):
            section_ids: set[Any] = set()
            if isinstance(section_inventory, dict):
                section_ids = {
                    section.get("id")
                    for section in section_inventory.get("sections", [])
                    if isinstance(section, dict)
                }
            section_status = (
                "passed" if normalized.get("status") == "passed"
                else "needs_revision"
            )
            normalized["section_reviews"] = [
                {
                    "section_id": section_id,
                    "status": section_status,
                    "missing_significant_content": [],
                    "distortions": [],
                }
                for section_id in covered
                if section_id in section_ids
            ]
        elif isinstance(section_inventory, dict):
            normalized["section_reviews"] = []

    normalized.pop("covered_section_ids", None)
    normalized.pop("covered_point_ids", None)
    return normalized


def normalize_summary_quality_payload(data: Any) -> Any:
    """Normalize former content-map payloads into the section-first contract.

    The compatibility path preserves old MCP clients while ensuring important
    points are not saved or fed forward as a summary content filter.
    """
    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    legacy_content_map = normalized.pop("content_map", None)
    if (
        "section_inventory" not in normalized
        and isinstance(legacy_content_map, dict)
    ):
        normalized["section_inventory"] = _legacy_section_inventory(
            legacy_content_map
        )
    inventory = normalized.get("section_inventory")
    if isinstance(inventory, dict):
        normalized["section_inventory"] = _sanitize_section_inventory(inventory)
    review = normalized.get("summary_review")
    if isinstance(review, dict):
        normalized["summary_review"] = _normalize_legacy_review(
            review,
            normalized.get("section_inventory"),
        )
    return normalized


def missing_summary_quality_fields(
    data: Any,
    *,
    chapter_text: str | None = None,
    chapter_title: str | None = None,
) -> list[str]:
    """Return invalid paths for section inventory and full-text review.

    No character-count rule is applied. Completion requires a structurally
    coherent inventory plus a passed review for every inventory section and
    for the chapter as a whole.
    """
    if not isinstance(data, dict):
        return ["data"]

    missing: list[str] = []
    inventory = data.get("section_inventory")
    if not isinstance(inventory, dict):
        return ["section_inventory", "summary_review"]

    has_subchapters = inventory.get("has_explicit_subchapters")
    if not isinstance(has_subchapters, bool):
        missing.append("section_inventory.has_explicit_subchapters")

    sections = inventory.get("sections")
    if not isinstance(sections, list) or not sections:
        missing.append("section_inventory.sections")
        sections = []

    section_ids: list[str] = []
    section_levels: dict[str, int] = {}
    explicit_flags: list[bool] = []
    explicit_headings: list[tuple[int, str]] = []
    for section_index, section in enumerate(sections):
        section_path = f"section_inventory.sections[{section_index}]"
        if not isinstance(section, dict):
            missing.append(section_path)
            continue

        section_id = section.get("id")
        valid_id = _valid_evidence_id(section_id)
        if not valid_id:
            missing.append(f"{section_path}.id")
        else:
            section_ids.append(section_id)

        heading = section.get("heading")
        if not _is_nonempty_str(heading):
            missing.append(f"{section_path}.heading")

        level = section.get("level")
        if not _is_positive_int(level):
            missing.append(f"{section_path}.level")
        elif valid_id:
            section_levels[section_id] = level

        parent_id = section.get("parent_id")
        if parent_id is not None and not _valid_evidence_id(parent_id):
            missing.append(f"{section_path}.parent_id")
        elif parent_id is None:
            if _is_positive_int(level) and level != 1:
                missing.append(f"{section_path}.level")
        elif parent_id not in section_levels:
            missing.append(f"{section_path}.parent_id")
        elif _is_positive_int(level) and section_levels[parent_id] >= level:
            missing.append(f"{section_path}.level")

        explicit = section.get("explicit_subchapter")
        if not isinstance(explicit, bool):
            missing.append(f"{section_path}.explicit_subchapter")
        else:
            explicit_flags.append(explicit)
            if explicit and _is_nonempty_str(heading):
                explicit_headings.append((section_index, heading))

    if len(section_ids) != len(set(section_ids)):
        missing.append("section_inventory.sections")
    if isinstance(has_subchapters, bool):
        if has_subchapters and (
            not explicit_flags or not all(explicit_flags)
        ):
            missing.append("section_inventory.sections")
        if not has_subchapters and (
            len(sections) != 1
            or explicit_flags != [False]
        ):
            missing.append("section_inventory.sections")

    if isinstance(chapter_text, str) and chapter_text.strip():
        _validate_source_heading_coverage(
            inventory, chapter_text, chapter_title, missing,
        )

    summary = data.get("summary")
    if explicit_headings and _is_nonempty_str(summary):
        markdown_headings = Counter(_markdown_heading_texts(summary))
        for section_index, heading in explicit_headings:
            normalized_heading = _normalize_visible_text(heading)
            if markdown_headings[normalized_heading] <= 0:
                missing.append(
                    f"section_inventory.sections[{section_index}].heading"
                )
            else:
                markdown_headings[normalized_heading] -= 1

    review = data.get("summary_review")
    if not isinstance(review, dict):
        missing.append("summary_review")
        return list(dict.fromkeys(missing))

    if review.get("status") != "passed":
        missing.append("summary_review.status")

    reviewed_against = _validate_string_list(
        review.get("reviewed_against"),
        "summary_review.reviewed_against",
        missing,
        allow_empty=False,
    )
    if (
        reviewed_against is not None
        and set(reviewed_against) != set(REVIEW_INPUTS)
    ):
        missing.append("summary_review.reviewed_against")

    section_reviews = review.get("section_reviews")
    reviewed_section_ids: list[str] = []
    if not isinstance(section_reviews, list) or not section_reviews:
        missing.append("summary_review.section_reviews")
        section_reviews = []
    for review_index, section_review in enumerate(section_reviews):
        review_path = f"summary_review.section_reviews[{review_index}]"
        if not isinstance(section_review, dict):
            missing.append(review_path)
            continue
        section_id = section_review.get("section_id")
        if not _valid_evidence_id(section_id):
            missing.append(f"{review_path}.section_id")
        else:
            reviewed_section_ids.append(section_id)
        if section_review.get("status") != "passed":
            missing.append(f"{review_path}.status")
        _validate_empty_review_findings(section_review, review_path, missing)

    if (
        len(reviewed_section_ids) != len(set(reviewed_section_ids))
        or set(reviewed_section_ids) != set(section_ids)
    ):
        missing.append("summary_review.section_reviews")

    _validate_empty_review_findings(review, "summary_review", missing)

    return list(dict.fromkeys(missing))
