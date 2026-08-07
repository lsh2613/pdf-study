"""Section-first summary structure and review validation.

The generation workflow inventories source structure before writing prose, then
reviews the full source text against each inventory section.  The inventory is
structure-only: it deliberately does not preselect important points that could
become an accidental ceiling on the resulting study summary.
"""
from __future__ import annotations

import re
from typing import Any


EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
REVIEW_INPUTS = ("chapter_text", "section_inventory", "draft_summary")


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


def missing_summary_quality_fields(data: Any) -> list[str]:
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
                    f"section_inventory.sections[{section_index}].heading"
                )

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
