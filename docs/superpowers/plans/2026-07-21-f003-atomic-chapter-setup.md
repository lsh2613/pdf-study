# F-003 Atomic Chapter Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development while implementing this plan task-by-task. This item is tightly coupled, so execute it inline rather than dispatching parallel implementers.

**Goal:** Make rejected `set_chapters` requests leave the workspace unchanged while recording a validated setup and its later extraction result as distinct state transitions.

**Architecture:** `analysis.set_chapters_impl` performs every recoverable preflight check and computes fallback metadata before any write. A new `workspace.commit_chapter_setup` helper writes book metadata and the complete setup state under one work lock, rolls book metadata back if state persistence fails, and starts `chapter_processing`. Extraction then runs outside the lock; its final phase is set to `completed` or `failed`, while chapter-level OCR errors remain available for retry.

**Tech Stack:** Python 3.11+, pytest, PyMuPDF, JSON files with atomic replace, per-work `threading.Lock`

## Global Constraints

- A rejected preflight request must preserve the exact pre-call `state.json` and `book_info.json` contents.
- `execution_mode`, `extraction_mode`, normalized chapters, `chapter_setup=completed`, and `chapter_processing=in_progress` must become visible through one `state.json` save under one work lock.
- Valid setup followed by extraction failure is not rolled back: the new setup remains current and the processing phase becomes `failed`.
- Calls for the same `work_id` must serialize setup commit, extraction, and phase finalization so stale processing cannot mutate a newer setup.
- A failed `book_info` rollback must be surfaced as a transaction error rather than logged and hidden.
- OCR chapter failure must keep `{chapter_id, failed_pages, error}`, must not save partial raw text, and must remain retryable.
- All `state.json` mutation must stay inside lock-protected `workspace.py` helpers.
- Do not touch or stage the user-owned untracked `docs/findings2.md`.
- Produce one final commit for F-003 after code, tests, and project documentation are complete.

---

### Task 1: Lock the rejected-request contract with regression tests

**Files:**
- Modify: `tests/test_analysis_e2e.py`

**Interfaces:**
- Consumes: `analysis.set_chapters_impl(...)`, `workspace.load_state(...)`
- Produces: regression coverage proving scan-before, out-of-range, and duplicate-ID failures do not mutate state

- [x] **Step 1: Strengthen the existing rejection tests**

Capture `before = workspace.load_state(wid)` before each rejected call and assert `workspace.load_state(wid) == before` afterward. For duplicate and out-of-range retry coverage, first create a valid chapter setup so the snapshot contains confirmed modes and existing chapter progress.

```python
before = workspace.load_state(wid)
with pytest.raises(ValueError, match="duplicate"):
    analysis.set_chapters_impl(wid, invalid_chapters, "parallel", "text")
assert workspace.load_state(wid) == before
```

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
rtk pytest -q tests/test_analysis_e2e.py -k 'set_chapters_rejects_out_of_range or set_chapters_rejects_duplicate_ids or set_chapters_requires_scan_first'
```

Expected: the state-equality assertions fail because modes are persisted before validation.

---

### Task 2: Add the atomic setup persistence boundary

**Files:**
- Modify: `workspace.py`
- Modify: `tests/test_workspace.py`

**Interfaces:**
- Produces: `commit_chapter_setup(work_id: str, chapters: list[dict[str, Any]], *, execution_mode: str, extraction_mode: str, book_info: dict[str, Any]) -> dict[str, Any]`
- Consumes: canonical chapter dictionaries containing `chapter_id`, `title`, `pdf_pages`, optional `source_pages`, and optional `skip`

- [x] **Step 1: Write failing workspace tests**

Add one test that records `save_state` calls and verifies a successful commit uses one state save containing all setup fields and phases. Add another that forces `save_state` to raise and verifies the previous `book_info.json` bytes and state remain unchanged.

```python
committed = workspace.commit_chapter_setup(
    wid,
    [{"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 1]}],
    execution_mode="parallel",
    extraction_mode="text",
    book_info={"title": "원문"},
)
assert committed["execution_mode"] == "parallel"
assert committed["phases"]["chapter_setup"] == "completed"
assert committed["phases"]["chapter_processing"] == "in_progress"
```

- [x] **Step 2: Run the new workspace tests and verify RED**

Run:

```bash
rtk pytest -q tests/test_workspace.py -k commit_chapter_setup
```

Expected: FAIL because `workspace.commit_chapter_setup` does not exist.

- [x] **Step 3: Extract chapter-state construction**

Move the dictionary-building loop from `set_chapters_in_state` into a private pure helper:

```python
def _build_chapter_state(chapters: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ...
```

Keep `set_chapters_in_state` behavior compatible by assigning this helper's result and completing only `chapter_setup`.

- [x] **Step 4: Implement `commit_chapter_setup`**

Under `_get_lock(work_id)`, load the current state, snapshot `book_info_path(work_id)`, write the new book info, assign modes and chapters, set `chapter_setup` to `completed`, set `chapter_processing` to `in_progress`, set `current_phase` to `chapter_processing`, and call `save_state` exactly once. Restore the book-info snapshot and re-raise if persistence fails.

- [x] **Step 5: Run the workspace tests and verify GREEN**

- [x] **Step 6: Surface rollback failures**

Force the book-info restore replace to fail after a state-save error. Verify RED when the original error hides the rollback failure, then make strict restore raise an explicit `chapter setup failed and book_info rollback failed` transaction error.

Run:

```bash
rtk pytest -q tests/test_workspace.py -k 'commit_chapter_setup or set_chapters_in_state'
```

Expected: PASS.

---

### Task 3: Separate preflight, setup commit, and extraction outcome

**Files:**
- Modify: `analysis.py`
- Modify: `tests/test_analysis_e2e.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `workspace.commit_chapter_setup(...)`
- Produces: `_process_chapters(work_id, normalized, pdf_path, extraction_mode) -> dict[str, Any]`

- [x] **Step 1: Add phase outcome assertions**

Extend a successful text extraction test to require `chapter_processing == "completed"`. Extend the existing OCR failure test to require `chapter_setup == "completed"` and `chapter_processing == "failed"` while retaining the failed chapter state.

- [x] **Step 2: Run the phase tests and verify RED**

Run:

```bash
rtk pytest -q tests/test_analysis_e2e.py tests/test_server.py -k 'set_chapters_text_mode or ocr_preprocessing_failure'
```

Expected: FAIL because `chapter_processing` remains `in_progress`.

- [x] **Step 3: Move all preflight work before persistence**

In `set_chapters_impl`, load state and reject missing `page_count`, normalize every chapter, reject duplicate IDs, and compute fallback `book_info` before calling any mutating helper.

- [x] **Step 4: Replace the split setup writes**

Remove the early `workspace.update_state`, `workspace.set_chapters_in_state`, `workspace.save_book_info`, and `workspace.update_phase(..., "in_progress")` calls. Replace them with one `workspace.commit_chapter_setup(...)` invocation.

- [x] **Step 5: Isolate processing and close the phase**

Move text/OCR extraction into `_process_chapters`. In `set_chapters_impl`, catch unexpected processing exceptions, set `chapter_processing=failed`, and re-raise. For normal returns, set the phase to `failed` when any non-skipped chapter has an error and otherwise to `completed`.

- [x] **Step 6: Serialize same-work setup processing**

Use a controlled `_process_chapters` in two threads to verify RED when setup B replaces setup A before A finishes. Add a per-work setup-session lock around preflight, commit, extraction, and phase finalization, then verify both calls complete in order.

- [x] **Step 7: Run focused tests and verify GREEN**

Run:

```bash
rtk pytest -q tests/test_workspace.py tests/test_analysis_e2e.py tests/test_server.py
```

Expected: PASS.

---

### Task 4: Align project documentation and complete F-003

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `docs/contracts.md`
- Modify: `docs/business-rules.md`
- Modify: `docs/architecture.md`
- Modify: `docs/engineering-notes.md`
- Modify: `docs/standards.md`
- Modify: `docs/tracking/status.md`
- Modify: `docs/findings.md`

**Interfaces:**
- Documents: rejected requests are side-effect free; valid setup is committed before extraction; processing failure remains diagnosable

- [x] **Step 1: Update the canonical rules**

Document the preflight/commit/processing boundary and the `chapter_processing` terminal statuses. Keep `AGENTS.md` and `CLAUDE.md` identical.

- [x] **Step 2: Mark F-003 resolved**

Correct the stale claim that empty `chapters` mutated state, record the chosen two-phase behavior, and leave F-011 untouched until the user explicitly advances.

- [x] **Step 3: Run documentation consistency checks**

Run:

```bash
rtk cmp -s AGENTS.md CLAUDE.md
rtk rg -n 'F-003|chapter_processing|set_chapters' docs AGENTS.md CLAUDE.md
```

Expected: `cmp` exits 0 and the search output matches the new contract.

---

### Task 5: Verify and commit the completed item

**Files:**
- Verify all modified files except `docs/findings2.md`

- [x] **Step 1: Run the full test suite**

Run:

```bash
rtk pytest -q
```

Expected: all tests pass; only already-known dependency warnings may remain.

- [x] **Step 2: Run repository hygiene checks**

Run:

```bash
rtk git diff --check
rtk cmp -s AGENTS.md CLAUDE.md
rtk git status --short
```

Expected: no whitespace errors, mirrored instructions match, and `docs/findings2.md` remains the only unrelated untracked file.

- [x] **Step 3: Review the diff against this plan**

Confirm rejected calls preserve state, validated setup is committed once, extraction outcomes close the processing phase, OCR diagnostics remain compatible, and F-011 has not been started.

- [x] **Step 4: Create the F-003 commit**

Stage only the approved F-003 files and commit with a repository-style message such as:

```text
fix: 챕터 확정 전 입력 검증 원자성 보장
```
