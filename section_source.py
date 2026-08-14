"""Build lossless, section-aligned views of canonical chapter text."""
from __future__ import annotations

from hashlib import sha256
import re
from typing import Any


SOURCE_BINDING_VERSION = "canonical-char-span-v1"
_SECTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_COPIED_SOURCE_FIELDS = frozenset({"content", "source_text", "important_points"})
_NUMBER_COMPONENT = r"[0-9Il\]\[]+"
_NUMBERED_HEADING_PATTERN = re.compile(
    rf"^(?:"
    rf"{_NUMBER_COMPONENT}(?:[.．·]{_NUMBER_COMPONENT})+[.)]?(?:\s+\S.*)?"
    rf"|{_NUMBER_COMPONENT}[.)](?:\s+\S.*)?"
    rf"|{_NUMBER_COMPONENT}\s+\S.*"
    rf")$"
)
_CANDIDATE_EXCLUSION_REASONS = frozenset({
    "toc_fragment",
    "cross_reference",
    "list_item",
    "table_or_figure",
    "header_or_footer",
    "not_a_heading",
})
SECTION_REVIEW_INPUTS = (
    "chapter_text",
    "section_inventory",
    "section_candidates",
)
_SECTION_REVIEW_FINDINGS = (
    "missing_sections",
    "false_sections",
    "hierarchy_issues",
    "unresolved_candidates",
)


class SectionSourceValidationError(ValueError):
    """The inventory cannot be bound losslessly to the canonical source."""

    def __init__(
        self,
        invalid_fields: list[str],
        *,
        details: dict[str, Any] | None = None,
    ):
        self.invalid_fields = list(dict.fromkeys(invalid_fields))
        self.details = dict(details or {})
        super().__init__(
            "section source를 canonical chapter text에 결합할 수 없습니다: "
            + ", ".join(self.invalid_fields)
        )


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _source_digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _line_start_occurrences(text: str, anchor: str) -> list[int]:
    """Return exact full-line anchor matches in the canonical source."""
    positions: list[int] = []
    search_from = 0
    while True:
        position = text.find(anchor, search_from)
        if position < 0:
            return positions
        end = position + len(anchor)
        begins_line = position == 0 or text[position - 1] == "\n"
        ends_line = (
            end == len(text)
            or text[end] in "\r\n"
            or anchor.endswith(("\r", "\n"))
        )
        if begins_line and ends_line:
            positions.append(position)
        search_from = position + 1


def detect_section_candidates(chapter_text: str) -> list[dict[str, Any]]:
    """Return high-recall numbered heading signals without deciding semantics.

    These candidates are deliberately limited to full lines that begin with a
    single-level or hierarchical number-like token.  The AI inventory analyst
    remains responsible for deciding whether each occurrence is a real section,
    a TOC fragment, or another false positive.
    """
    if not isinstance(chapter_text, str):
        return []
    occurrences: dict[str, int] = {}
    candidates: list[dict[str, Any]] = []
    for raw_line in chapter_text.splitlines():
        exact_line = raw_line.rstrip("\r")
        stripped = exact_line.strip()
        if not stripped or len(stripped) > 200:
            continue
        if _NUMBERED_HEADING_PATTERN.fullmatch(stripped) is None:
            continue
        occurrences[exact_line] = occurrences.get(exact_line, 0) + 1
        candidates.append({
            "text": exact_line,
            "occurrence": occurrences[exact_line],
        })
    return candidates


def _anchor_offset(
    chapter_text: str,
    anchor: Any,
    path: str,
    invalid: list[str],
    matches_by_anchor: dict[str, list[int]] | None = None,
) -> int | None:
    if not isinstance(anchor, dict):
        invalid.append(path)
        return None
    anchor_text = anchor.get("text")
    occurrence = anchor.get("occurrence")
    if not _is_nonempty_str(anchor_text):
        invalid.append(f"{path}.text")
    if not _is_positive_int(occurrence):
        invalid.append(f"{path}.occurrence")
    if not _is_nonempty_str(anchor_text) or not _is_positive_int(occurrence):
        return None
    matches = None
    if matches_by_anchor is not None:
        matches = matches_by_anchor.get(anchor_text)
    if matches is None:
        matches = _line_start_occurrences(chapter_text, anchor_text)
        if matches_by_anchor is not None:
            matches_by_anchor[anchor_text] = matches
    if occurrence > len(matches):
        invalid.append(f"{path}.match")
        return None
    return matches[occurrence - 1]


def audit_section_inventory(
    chapter_text: str,
    section_inventory: Any,
) -> dict[str, Any]:
    """Fail closed when strong heading candidates are silently unaccounted for."""
    if not _is_nonempty_str(chapter_text):
        raise SectionSourceValidationError(["chapter_text"])
    has_subchapters, sections = _validate_inventory_shape(section_inventory)
    candidates = detect_section_candidates(chapter_text)
    candidate_offsets: dict[int, dict[str, Any]] = {}
    invalid: list[str] = []
    matches_by_anchor: dict[str, list[int]] = {}
    for candidate in candidates:
        offset = _anchor_offset(
            chapter_text,
            candidate,
            "section_candidates",
            invalid,
            matches_by_anchor,
        )
        if offset is not None:
            candidate_offsets[offset] = candidate

    selected_offsets: set[int] = set()
    if has_subchapters:
        for index, section in enumerate(sections):
            offset = _anchor_offset(
                chapter_text,
                section.get("source_anchor"),
                f"section_inventory.sections[{index}].source_anchor",
                invalid,
                matches_by_anchor,
            )
            if offset is not None:
                selected_offsets.add(offset)

    raw_exclusions = section_inventory.get("candidate_exclusions", [])
    excluded_offsets: set[int] = set()
    if not isinstance(raw_exclusions, list):
        invalid.append("section_inventory.candidate_exclusions")
        raw_exclusions = []
    for index, exclusion in enumerate(raw_exclusions):
        path = f"section_inventory.candidate_exclusions[{index}]"
        if not isinstance(exclusion, dict):
            invalid.append(path)
            continue
        reason = exclusion.get("reason")
        if reason not in _CANDIDATE_EXCLUSION_REASONS:
            invalid.append(f"{path}.reason")
        offset = _anchor_offset(
            chapter_text,
            exclusion.get("source_anchor"),
            f"{path}.source_anchor",
            invalid,
            matches_by_anchor,
        )
        if offset is None:
            continue
        if offset not in candidate_offsets:
            invalid.append(f"{path}.source_anchor.candidate")
        if offset in selected_offsets or offset in excluded_offsets:
            invalid.append(f"{path}.source_anchor.duplicate")
        excluded_offsets.add(offset)

    unaccounted = [
        candidate
        for offset, candidate in candidate_offsets.items()
        if offset not in selected_offsets and offset not in excluded_offsets
    ]
    if unaccounted and not invalid:
        invalid.append("section_inventory.candidate_audit.unaccounted")
    if invalid:
        raise SectionSourceValidationError(
            invalid,
            details={
                "section_candidates": candidates,
                "unaccounted_candidates": unaccounted,
            },
        )
    return {
        "complete": True,
        "section_candidates": candidates,
        "unaccounted_candidates": [],
        "review_required": not has_subchapters or bool(raw_exclusions),
    }


def invalid_section_review_fields(
    section_review: Any,
    *,
    required: bool,
) -> list[str]:
    """Validate a conditional, structure-only independent review payload."""
    if section_review is None:
        return ["section_review"] if required else []
    if not isinstance(section_review, dict):
        return ["section_review"]

    invalid: list[str] = []
    if section_review.get("status") != "passed":
        invalid.append("section_review.status")
    if section_review.get("reviewed_against") != list(SECTION_REVIEW_INPUTS):
        invalid.append("section_review.reviewed_against")
    for field in _SECTION_REVIEW_FINDINGS:
        value = section_review.get(field)
        if not isinstance(value, list) or value:
            invalid.append(f"section_review.{field}")
    return invalid


def section_review_example() -> dict[str, Any]:
    return {
        "status": "passed",
        "reviewed_against": list(SECTION_REVIEW_INPUTS),
        "missing_sections": [],
        "false_sections": [],
        "hierarchy_issues": [],
        "unresolved_candidates": [],
    }


def _validate_inventory_shape(inventory: Any) -> tuple[bool, list[dict[str, Any]]]:
    invalid: list[str] = []
    if not isinstance(inventory, dict):
        raise SectionSourceValidationError(["section_inventory"])

    has_subchapters = inventory.get("has_explicit_subchapters")
    if not isinstance(has_subchapters, bool):
        invalid.append("section_inventory.has_explicit_subchapters")

    raw_sections = inventory.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        invalid.append("section_inventory.sections")
        raise SectionSourceValidationError(invalid)

    sections: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    section_levels: dict[str, int] = {}
    for index, raw_section in enumerate(raw_sections):
        path = f"section_inventory.sections[{index}]"
        if not isinstance(raw_section, dict):
            invalid.append(path)
            continue
        section = dict(raw_section)
        sections.append(section)

        section_id = section.get("id")
        if (
            not _is_nonempty_str(section_id)
            or _SECTION_ID_PATTERN.fullmatch(section_id) is None
            or section_id in seen_ids
        ):
            invalid.append(f"{path}.id")
        else:
            seen_ids.add(section_id)

        if not _is_nonempty_str(section.get("heading")):
            invalid.append(f"{path}.heading")

        level = section.get("level")
        if not _is_positive_int(level):
            invalid.append(f"{path}.level")

        parent_id = section.get("parent_id")
        if parent_id is None:
            if _is_positive_int(level) and level != 1:
                invalid.append(f"{path}.level")
        elif not _is_nonempty_str(parent_id) or parent_id not in section_levels:
            invalid.append(f"{path}.parent_id")
        elif _is_positive_int(level) and section_levels[parent_id] >= level:
            invalid.append(f"{path}.level")

        explicit = section.get("explicit_subchapter")
        if not isinstance(explicit, bool):
            invalid.append(f"{path}.explicit_subchapter")
        elif isinstance(has_subchapters, bool) and explicit is not has_subchapters:
            invalid.append(f"{path}.explicit_subchapter")

        if (
            _is_nonempty_str(section_id)
            and _SECTION_ID_PATTERN.fullmatch(section_id) is not None
            and _is_positive_int(level)
        ):
            section_levels[section_id] = level

    if has_subchapters is False and len(raw_sections) != 1:
        invalid.append("section_inventory.sections")
    if invalid:
        raise SectionSourceValidationError(invalid)
    return has_subchapters, sections


def _sanitize_section(section: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in section.items()
        if key not in _COPIED_SOURCE_FIELDS and key != "source_span"
    }


def _region_metadata(
    *,
    kind: str,
    section: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    if section is None:
        return {
            "kind": kind,
            "section_id": None,
            "heading": None,
            "level": 0,
            "parent_id": None,
            "render_heading": False,
            "source_span": [start, end],
        }
    return {
        "kind": kind,
        "section_id": section["id"],
        "heading": section["heading"],
        "level": section["level"],
        "parent_id": section.get("parent_id"),
        "render_heading": section.get("explicit_subchapter") is True,
        "source_span": [start, end],
    }


def _prepare_section_metadata(
    chapter_text: str,
    section_inventory: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    if not _is_nonempty_str(chapter_text):
        raise SectionSourceValidationError(["chapter_text"])
    has_subchapters, sections = _validate_inventory_shape(section_inventory)

    if not has_subchapters:
        offsets = [0]
    else:
        invalid: list[str] = []
        offsets: list[int] = []
        previous_offset = -1
        matches_by_anchor: dict[str, list[int]] = {}
        for index, section in enumerate(sections):
            path = f"section_inventory.sections[{index}].source_anchor"
            offset = _anchor_offset(
                chapter_text,
                section.get("source_anchor"),
                path,
                invalid,
                matches_by_anchor,
            )
            if offset is None:
                continue
            if offset <= previous_offset:
                invalid.append(f"{path}.order")
                continue
            offsets.append(offset)
            previous_offset = offset
        if invalid:
            raise SectionSourceValidationError(invalid)

    region_metadata: list[dict[str, Any]] = []
    if has_subchapters and offsets[0] > 0:
        region_metadata.append(_region_metadata(
            kind="preamble",
            section=None,
            start=0,
            end=offsets[0],
        ))

    enriched_sections: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        start = offsets[index]
        end = offsets[index + 1] if index + 1 < len(offsets) else len(chapter_text)
        enriched = _sanitize_section(section)
        enriched["source_span"] = [start, end]
        enriched_sections.append(enriched)
        region_metadata.append(_region_metadata(
            kind="section" if has_subchapters else "chapter",
            section=enriched,
            start=start,
            end=end,
        ))

    binding_regions = [
        {
            "kind": region["kind"],
            "section_id": region["section_id"],
            "source_span": list(region["source_span"]),
        }
        for region in region_metadata
    ]
    enriched_inventory = {
        key: value
        for key, value in section_inventory.items()
        if key not in {
            "sections",
            "source_binding",
            "structured_sections",
            "candidate_exclusions",
        }
    }
    enriched_inventory["sections"] = enriched_sections
    source_digest = _source_digest(chapter_text)
    enriched_inventory["source_binding"] = {
        "version": SOURCE_BINDING_VERSION,
        "source_char_count": len(chapter_text),
        "source_sha256": source_digest,
        "regions": binding_regions,
    }
    return enriched_inventory, region_metadata, source_digest


def prepare_section_source(
    chapter_text: str,
    section_inventory: Any,
) -> dict[str, Any]:
    """Bind inventory headings to exact spans and return lossless source regions."""
    enriched_inventory, region_metadata, source_digest = (
        _prepare_section_metadata(chapter_text, section_inventory)
    )
    structured_sections = [
        {
            **region,
            "source_text": chapter_text[
                region["source_span"][0]:region["source_span"][1]
            ],
        }
        for region in region_metadata
    ]
    return {
        "section_inventory": enriched_inventory,
        "structured_sections": structured_sections,
        "source_char_count": len(chapter_text),
        "source_sha256": source_digest,
        "coverage": {
            "complete": True,
            "start": 0,
            "end": len(chapter_text),
            "region_count": len(structured_sections),
        },
    }


def invalid_source_binding_fields(
    section_inventory: Any,
    chapter_text: str,
) -> list[str]:
    """Validate an optional prepared binding without judging summary structure."""
    if not isinstance(section_inventory, dict):
        return []
    binding = section_inventory.get("source_binding")
    if binding is None:
        return []
    if not isinstance(binding, dict):
        return ["section_inventory.source_binding"]

    try:
        expected_inventory, _, _ = _prepare_section_metadata(
            chapter_text, section_inventory,
        )
    except SectionSourceValidationError as exc:
        return exc.invalid_fields

    invalid: list[str] = []
    base = "section_inventory.source_binding"
    expected_binding = expected_inventory["source_binding"]
    if binding.get("version") != expected_binding["version"]:
        invalid.append(f"{base}.version")
    if (
        binding.get("source_char_count")
        != expected_binding["source_char_count"]
    ):
        invalid.append(f"{base}.source_char_count")
    if binding.get("source_sha256") != expected_binding["source_sha256"]:
        invalid.append(f"{base}.source_sha256")
    if binding.get("regions") != expected_binding["regions"]:
        invalid.append(f"{base}.regions")

    sections = section_inventory.get("sections")
    expected_sections = expected_inventory["sections"]
    for index, section in enumerate(sections):
        if section.get("source_span") != expected_sections[index]["source_span"]:
            invalid.append(f"section_inventory.sections[{index}].source_span")

    return list(dict.fromkeys(invalid))
