"""Section-guided summary generation and semantic review validation.

The generation workflow inventories source structure before writing prose so
the summary agent can preserve every explicit subchapter.  Once the draft has
been created, validation intentionally does not re-interpret or re-check that
section structure.  The independent review remains responsible for semantic
omissions and distortions across the full chapter text.
"""
from __future__ import annotations

from typing import Any


REVIEW_INPUTS = ("chapter_text", "draft_summary")


def quality_payload_example() -> dict[str, Any]:
    """Return a fresh, minimal passing inventory and semantic review payload."""
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


def _normalize_legacy_review(review: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(review)
    reviewed_against = normalized.get("reviewed_against")
    if isinstance(reviewed_against, list):
        normalized["reviewed_against"] = [
            value
            for value in reviewed_against
            if value not in ("content_map", "section_inventory")
        ]

    # These fields belonged to the former section-by-section validation gate.
    # Accept old clients, but do not persist obsolete structural review claims.
    normalized.pop("section_reviews", None)
    normalized.pop("covered_section_ids", None)
    normalized.pop("covered_point_ids", None)
    return normalized


def normalize_summary_quality_payload(data: Any) -> Any:
    """Normalize legacy payloads into the section-guided summary contract."""
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
        normalized["summary_review"] = _normalize_legacy_review(review)
    return normalized


def missing_summary_quality_fields(
    data: Any,
    *,
    chapter_text: str | None = None,
    chapter_title: str | None = None,
) -> list[str]:
    """Return invalid paths for required generation evidence and review.

    ``chapter_text`` and ``chapter_title`` remain accepted for internal caller
    compatibility, but section structure is intentionally not revalidated after
    summary generation.
    """
    del chapter_text, chapter_title
    if not isinstance(data, dict):
        return ["data"]

    missing: list[str] = []
    if not isinstance(data.get("section_inventory"), dict):
        missing.append("section_inventory")

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

    _validate_empty_review_findings(review, "summary_review", missing)
    return list(dict.fromkeys(missing))
