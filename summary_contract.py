"""Summary completeness evidence and validation.

The server cannot judge prose quality from length.  Instead, the generation
workflow first inventories meaningful source content and then records an
independent coverage review of the final draft.  This module validates that
evidence before a chapter may be marked completed.
"""
from __future__ import annotations

import copy
import re
from typing import Any


EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
REVIEW_INPUTS = ("chapter_text", "content_map", "draft_summary")


def quality_payload_example() -> dict[str, Any]:
    """Return a fresh, minimal passing quality-evidence payload."""
    return copy.deepcopy({
        "content_map": {
            "has_explicit_subchapters": False,
            "sections": [{
                "id": "section_1",
                "heading": "챕터 전체",
                "explicit_subchapter": False,
                "important_points": [{
                    "id": "point_1",
                    "content": "원문에서 빠뜨리면 안 되는 핵심 내용",
                    "significance": "이 내용이 챕터의 의미 전달에 중요한 이유",
                }],
            }],
        },
        "summary_review": {
            "status": "passed",
            "reviewed_against": list(REVIEW_INPUTS),
            "covered_section_ids": ["section_1"],
            "covered_point_ids": ["point_1"],
            "missing_significant_content": [],
            "distortions": [],
        },
    })


def content_map_example() -> dict[str, Any]:
    return quality_payload_example()["content_map"]


def summary_review_example() -> dict[str, Any]:
    return quality_payload_example()["summary_review"]


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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


def missing_summary_quality_fields(data: Any) -> list[str]:
    """Return invalid paths for content inventory and coverage review.

    No character-count rule is applied.  Completion depends on a non-empty
    source-content inventory and an explicit review covering every inventory
    section and point without known omissions or distortions.
    """
    if not isinstance(data, dict):
        return ["data"]

    missing: list[str] = []
    content_map = data.get("content_map")
    if not isinstance(content_map, dict):
        return ["content_map", "summary_review"]

    has_subchapters = content_map.get("has_explicit_subchapters")
    if not isinstance(has_subchapters, bool):
        missing.append("content_map.has_explicit_subchapters")

    sections = content_map.get("sections")
    if not isinstance(sections, list) or not sections:
        missing.append("content_map.sections")
        sections = []

    section_ids: list[str] = []
    point_ids: list[str] = []
    explicit_flags: list[bool] = []
    explicit_headings: list[tuple[int, str]] = []
    for section_index, section in enumerate(sections):
        section_path = f"content_map.sections[{section_index}]"
        if not isinstance(section, dict):
            missing.append(section_path)
            continue

        section_id = section.get("id")
        if not _valid_evidence_id(section_id):
            missing.append(f"{section_path}.id")
        else:
            section_ids.append(section_id)

        if not _is_nonempty_str(section.get("heading")):
            missing.append(f"{section_path}.heading")

        explicit = section.get("explicit_subchapter")
        if not isinstance(explicit, bool):
            missing.append(f"{section_path}.explicit_subchapter")
        else:
            explicit_flags.append(explicit)
            if explicit and _is_nonempty_str(section.get("heading")):
                explicit_headings.append((section_index, section["heading"]))

        points = section.get("important_points")
        if not isinstance(points, list) or not points:
            missing.append(f"{section_path}.important_points")
            continue
        for point_index, point in enumerate(points):
            point_path = f"{section_path}.important_points[{point_index}]"
            if not isinstance(point, dict):
                missing.append(point_path)
                continue
            point_id = point.get("id")
            if not _valid_evidence_id(point_id):
                missing.append(f"{point_path}.id")
            else:
                point_ids.append(point_id)
            if not _is_nonempty_str(point.get("content")):
                missing.append(f"{point_path}.content")
            if not _is_nonempty_str(point.get("significance")):
                missing.append(f"{point_path}.significance")

    if len(section_ids) != len(set(section_ids)):
        missing.append("content_map.sections")
    if len(point_ids) != len(set(point_ids)):
        missing.append("content_map.sections")
    if isinstance(has_subchapters, bool):
        if has_subchapters and not any(explicit_flags):
            missing.append("content_map.sections")
        if not has_subchapters and (
            len(sections) != 1 or any(explicit_flags)
        ):
            missing.append("content_map.sections")

    summary = data.get("summary")
    if explicit_headings and _is_nonempty_str(summary):
        markdown_headings = _markdown_heading_texts(summary)
        for section_index, heading in explicit_headings:
            normalized_heading = _normalize_visible_text(heading)
            if not any(
                normalized_heading in rendered_heading
                for rendered_heading in markdown_headings
            ):
                missing.append(
                    f"content_map.sections[{section_index}].heading"
                )

    review = data.get("summary_review")
    if not isinstance(review, dict):
        missing.append("summary_review")
        return missing

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

    covered_section_ids = _validate_string_list(
        review.get("covered_section_ids"),
        "summary_review.covered_section_ids",
        missing,
        allow_empty=False,
    )
    if (
        covered_section_ids is not None
        and set(covered_section_ids) != set(section_ids)
    ):
        missing.append("summary_review.covered_section_ids")

    covered_point_ids = _validate_string_list(
        review.get("covered_point_ids"),
        "summary_review.covered_point_ids",
        missing,
        allow_empty=False,
    )
    if (
        covered_point_ids is not None
        and set(covered_point_ids) != set(point_ids)
    ):
        missing.append("summary_review.covered_point_ids")

    omissions = _validate_string_list(
        review.get("missing_significant_content"),
        "summary_review.missing_significant_content",
        missing,
        allow_empty=True,
    )
    if omissions:
        missing.append("summary_review.missing_significant_content")

    distortions = _validate_string_list(
        review.get("distortions"),
        "summary_review.distortions",
        missing,
        allow_empty=True,
    )
    if distortions:
        missing.append("summary_review.distortions")

    # Preserve stable error ordering while avoiding duplicate aggregate paths.
    return list(dict.fromkeys(missing))
