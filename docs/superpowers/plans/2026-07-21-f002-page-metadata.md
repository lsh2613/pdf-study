# F-002 Page Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace ambiguous chapter page keys with `pdf_pages` and `source_pages`, preserve the source-page metadata end to end, and label both page systems clearly in HTML and Markdown+TUI output.

**Architecture:** `pdf_pages` is the canonical 1-based inclusive PDF extraction boundary and `source_pages` is optional display metadata for the page number printed in the source document. New responses and persisted data use only the canonical keys; normalization and renderer loading accept legacy `page_range` and `printed_range` so existing calls and resumable work remain readable.

**Tech Stack:** Python 3.11+, FastMCP, PyMuPDF, pytest, HTML, Markdown

## Global Constraints

- Chapter boundaries continue to come only from PDF bookmarks or rendered table-of-contents images.
- Extraction always uses `pdf_pages`; `source_pages` never changes the PDF pages read.
- Public page numbers remain 1-based and inclusive.
- `state.json` changes continue to use lock-protected `workspace.py` helpers.
- F-003 state-transition ordering is out of scope.
- Existing `page_range` and `printed_range` inputs and persisted work remain readable, but new output and storage use canonical keys.

---

### Task 1: Canonical chapter page contract

**Files:**
- Modify: `tests/test_recommendations.py`
- Modify: `tests/test_server.py`
- Modify: `tests/test_workspace.py`
- Modify: `analysis.py`
- Modify: `workspace.py`
- Modify: `server.py`
- Modify: `pdf/chapter.py`

**Interfaces:**
- Consumes: chapter dictionaries with canonical `pdf_pages`/`source_pages` or legacy input aliases.
- Produces: recommendations, state entries, raw chapter JSON, and `set_chapters` responses using canonical keys.

- [x] **Step 1: Write failing canonical-contract tests**

```python
chapter = {
    "chapter_id": "ch1",
    "title": "본문",
    "pdf_pages": [19, 23],
    "source_pages": [1, 5],
}
result = server.set_chapters(
    work_id,
    [chapter],
    execution_mode="sequential",
    extraction_mode="text",
)
assert result["data"]["chapters"][0]["pdf_pages"] == [19, 23]
assert result["data"]["chapters"][0]["source_pages"] == [1, 5]
assert workspace.load_state(work_id)["chapters"]["ch1"]["source_pages"] == [1, 5]
assert workspace.get_chapter_raw(work_id, "ch1")["source_pages"] == [1, 5]
```

- [x] **Step 2: Run tests and verify the expected missing-key failures**

Run: `.venv/bin/python -m pytest -q tests/test_recommendations.py tests/test_workspace.py tests/test_server.py`

Expected: failures showing that current recommendations and persisted chapter data still expose `page_range`/`printed_range` and reject `pdf_pages`.

- [x] **Step 3: Implement canonical normalization and storage**

```python
def _chapter_pages(chapter: dict[str, Any]) -> Any:
    return chapter.get("pdf_pages", chapter.get("page_range"))


def _source_pages(chapter: dict[str, Any]) -> tuple[bool, Any]:
    if "source_pages" in chapter:
        return True, chapter["source_pages"]
    if "printed_range" in chapter:
        return True, chapter["printed_range"]
    return False, None
```

Normalize recommendations and all new state/raw/response payloads to `pdf_pages` and `source_pages`. Preserve explicit `source_pages=None`, propagate it through OCR cache reuse, and rename available-range metadata to `pdf_pages_available` and `source_pages_available`.

- [x] **Step 4: Add legacy input and resumable-state coverage**

```python
legacy = {"chapter_id": "ch1", "title": "본문", "page_range": [1, 2], "printed_range": [1, 2]}
normalized = analysis._validate_chapter_def(legacy, page_count=2)
assert normalized["pdf_pages"] == [1, 2]
assert normalized["source_pages"] == [1, 2]
assert "page_range" not in normalized
assert "printed_range" not in normalized
```

- [x] **Step 5: Run canonical-contract tests**

Run: `.venv/bin/python -m pytest -q tests/test_recommendations.py tests/test_workspace.py tests/test_server.py tests/test_analysis_e2e.py tests/test_chapter_chunks.py tests/test_skip_chapter.py`

Expected: all selected tests pass.

### Task 2: Shared page-label rendering

**Files:**
- Create: `renderer/page_labels.py`
- Modify: `renderer/html_renderer.py`
- Modify: `renderer/md_tui_renderer.py`
- Modify: `tests/test_renderer.py`
- Modify: `tests/test_md_tui_renderer.py`

**Interfaces:**
- Consumes: chapter metadata and state `page_offset`.
- Produces: one plain-text label shared by both renderers.

- [x] **Step 1: Write failing renderer tests**

```python
assert "PDF p.19–23 · 원문 p.1–5" in chapter_html
assert "PDF p.19–23 · 원문 p.1–5" in book_markdown
assert "PDF p.1–12 · 원문 페이지 미상" in unknown_offset_output
assert "PDF p.1–18 · 원문 페이지 없음" in front_matter_output
```

- [x] **Step 2: Run tests and verify current `p.N–M` output fails them**

Run: `.venv/bin/python -m pytest -q tests/test_renderer.py tests/test_md_tui_renderer.py`

Expected: failures because current renderers omit `PDF`/`원문` labels.

- [x] **Step 3: Implement a shared label helper and use it everywhere**

```python
def format_page_label(meta: dict[str, Any], *, page_offset: int | None) -> str:
    pdf_pages = meta.get("pdf_pages", meta.get("page_range"))
    source_present = "source_pages" in meta or "printed_range" in meta
    source_pages = meta.get("source_pages", meta.get("printed_range"))
    label = f"PDF p.{pdf_pages[0]}–{pdf_pages[1]}"
    if not source_present:
        return label
    if source_pages is not None:
        return f"{label} · 원문 p.{source_pages[0]}–{source_pages[1]}"
    suffix = "원문 페이지 미상" if page_offset is None else "원문 페이지 없음"
    return f"{label} · {suffix}"
```

Use the helper for HTML index rows, HTML chapter headers, `book.md`, and chapter `summary.md`.

- [x] **Step 4: Run renderer tests**

Run: `.venv/bin/python -m pytest -q tests/test_renderer.py tests/test_md_tui_renderer.py`

Expected: all renderer tests pass.

### Task 3: Documentation and F-002 closure

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `docs/contracts.md`
- Modify: `docs/business-rules.md`
- Modify: `docs/architecture.md`
- Modify: `docs/findings.md`

**Interfaces:**
- Consumes: verified canonical behavior from Tasks 1 and 2.
- Produces: matching external contract, business terminology, architecture description, and resolved finding record.

- [x] **Step 1: Update terminology and compatibility documentation**

Document `pdf_pages` as the physical PDF range, `source_pages` as display-only source numbering, `PDF`/`원문` renderer labels, and legacy read/input compatibility. Remove the old `page_range`, `printed_range`, and book-page terminology from current contract text except where documenting aliases.

- [x] **Step 2: Record F-002 as resolved**

Update `docs/findings.md` with the decision, code/data flow, renderer behavior, compatibility boundary, test evidence, and completion date. Do not advance to F-003.

- [x] **Step 3: Verify instruction-entry parity**

Run: `cmp -s AGENTS.md CLAUDE.md`

Expected: exit code 0.

### Task 4: Full verification and commit

**Files:**
- Verify all files changed by Tasks 1–3.

**Interfaces:**
- Consumes: complete F-002 implementation and documentation.
- Produces: one reviewed commit containing only F-002 changes and its plan.

- [x] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`

Expected: all collected tests pass; existing third-party deprecation warnings may remain.

- [x] **Step 2: Inspect contract terminology and diff**

Run: `rg -n "page_range|printed_range|책 페이지|물리 페이지" analysis.py server.py workspace.py pdf renderer docs AGENTS.md CLAUDE.md`

Expected: old keys occur only in documented legacy compatibility code/tests or unrelated historical findings.

Run: `git diff --check`

Expected: no whitespace errors.

- [x] **Step 3: Commit only F-002 files**

Commit message:

```text
fix: 페이지 메타데이터 계약을 명확히 보존
```

- [x] **Step 4: Confirm repository status and commit**

Run: `git status --short`

Expected: only the pre-existing untracked `docs/findings2.md` remains.
