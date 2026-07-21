# F-011 Pending-Only Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make resumed work expose and process only the summary and extension results that are not already completed.

**Architecture:** A pure `workspace.pending_chapters_from_state` helper becomes the single pending-status projection for one state snapshot. Prompt construction exposes separate summary and extension lists while retaining `chapter_ids` as their compatibility union; raw validation and every workflow hint consume the same pending projection.

**Tech Stack:** Python 3.11+, pytest, FastMCP response dictionaries, JSON workspace state

## Global Constraints

- `summary_pending_chapter_ids` and `extension_pending_chapter_ids` are the canonical prompt processing lists.
- `chapter_ids` remains present as the naturally sorted union of both pending lists.
- `completed` and `skipped` are done; `pending`, `failed`, and `in_progress` remain pending.
- Disabled extension produces an empty extension pending list.
- Only pending chapters require valid raw `text` and `char_count` from `get_subagent_prompts`.
- Do not change the state schema or forbid an explicitly requested overwrite of a completed result.
- Preserve the `{ok, error, data, next_action}` response envelope and existing recoverable error shapes.
- Keep `AGENTS.md` and `CLAUDE.md` byte-identical.
- Do not read, edit, stage, or commit the user-owned untracked `docs/findings2.md`.
- Produce task-scoped reviewed commits and leave the branch at one complete F-011 state after the final verification commit.

---

### Task 1: Centralize pending-status projection

**Files:**
- Modify: `workspace.py:882-904`
- Modify: `tests/test_workspace.py:466-478`

**Interfaces:**
- Produces: `pending_chapters_from_state(state: dict[str, Any]) -> dict[str, list[str]]`
- Preserves: `list_pending_chapters_impl(work_id: str) -> dict[str, list[str]]`

- [x] **Step 1: Write failing pure-helper tests**

Add state-only tests covering asymmetric statuses, skip, natural ordering, and disabled extension:

```python
def test_pending_chapters_from_state_splits_result_types():
    state = {
        "question_options": {"extension": True},
        "chapters": {
            "ch10": {"summary_status": "pending", "extension_status": "completed"},
            "ch2": {"summary_status": "completed", "extension_status": "failed"},
            "ch1": {"summary_status": "completed", "extension_status": "completed"},
            "appendix": {"skip": True, "summary_status": "skipped", "extension_status": "skipped"},
        },
    }
    assert workspace.pending_chapters_from_state(state) == {
        "summary_pending": ["ch10"],
        "extension_pending": ["ch2"],
    }


def test_pending_chapters_from_state_ignores_disabled_extension():
    state = {
        "question_options": {"extension": False},
        "chapters": {
            "ch1": {"summary_status": "completed", "extension_status": "pending"},
        },
    }
    assert workspace.pending_chapters_from_state(state)["extension_pending"] == []
```

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_workspace.py -k pending_chapters
```

Expected: FAIL because `pending_chapters_from_state` does not exist or the old helper does not filter disabled extension and natural-sort IDs.

- [x] **Step 3: Implement the pure projection and delegate the disk helper**

Use one ordered pass over the state snapshot:

```python
def _chapter_sort_key(chapter_id: str) -> tuple[int, str]:
    if chapter_id.startswith("ch") and chapter_id[2:].isdigit():
        return (int(chapter_id[2:]), chapter_id)
    return (10**9, chapter_id)


def pending_chapters_from_state(state: dict[str, Any]) -> dict[str, list[str]]:
    extension_enabled = bool(state.get("question_options", {}).get("extension"))
    summary_pending: list[str] = []
    extension_pending: list[str] = []
    for chapter_id in sorted(state.get("chapters", {}), key=_chapter_sort_key):
        entry = state["chapters"][chapter_id]
        if entry.get("skip"):
            continue
        if entry.get("summary_status") not in _DONE_STATUSES:
            summary_pending.append(chapter_id)
        if extension_enabled and entry.get("extension_status") not in _DONE_STATUSES:
            extension_pending.append(chapter_id)
    return {
        "summary_pending": summary_pending,
        "extension_pending": extension_pending,
    }


def list_pending_chapters_impl(work_id: str) -> dict[str, list[str]]:
    return pending_chapters_from_state(load_state(work_id))
```

- [x] **Step 4: Run workspace tests and verify GREEN**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_workspace.py -k pending_chapters
```

Expected: PASS.

---

### Task 2: Expose separate prompt lists and validate only their union

**Files:**
- Modify: `prompts.py:212-358`
- Modify: `analysis.py:965-997`
- Modify: `server.py:971-1005`
- Modify: `tests/test_prompts.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Extends: `analysis.validate_chapter_raw_inputs(work_id, state=None, chapter_ids=None)`
- Produces response fields: `summary_pending_chapter_ids`, `extension_pending_chapter_ids`
- Narrows compatibility field: `chapter_ids` to the natural-sort union of pending lists

- [x] **Step 1: Write failing prompt projection tests**

Build an asymmetric state and assert exact structured lists:

```python
def test_prompt_chapter_ids_are_pending_union_by_result_type():
    state = _state(chapters={
        "ch1": {"summary_status": "completed", "extension_status": "pending"},
        "ch2": {"summary_status": "pending", "extension_status": "completed"},
        "ch3": {"summary_status": "completed", "extension_status": "completed"},
    })
    out = prompts.build_prompts(state)
    assert out["summary_pending_chapter_ids"] == ["ch2"]
    assert out["extension_pending_chapter_ids"] == ["ch1"]
    assert out["chapter_ids"] == ["ch1", "ch2"]
```

Also assert both sequential and parallel instructions name the two canonical lists and say that only the requested result kind is saved.

- [x] **Step 2: Write failing server raw-validation tests**

Create two chapters, complete `ch1`, damage or remove only `ch1` raw, and verify `get_subagent_prompts` still returns prompts for pending `ch2`. Then damage `ch2` raw and verify the existing structured invalid response names only `ch2`.

```python
response = server.get_subagent_prompts(work_id)
assert response["ok"] is True
assert response["data"]["chapter_ids"] == ["ch2"]
assert response["data"]["summary_pending_chapter_ids"] == ["ch2"]
```

- [x] **Step 3: Run the focused tests and verify RED**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_prompts.py tests/test_server.py -k 'pending_union or pending_raw or workflow_instructions'
```

Expected: FAIL because prompt output has no separate pending fields, includes completed chapters, and validates every non-skip raw file.

- [x] **Step 4: Filter raw validation by an optional ID set**

Extend the analysis boundary without changing default callers:

```python
def validate_chapter_raw_inputs(
    work_id: str,
    state: dict[str, Any] | None = None,
    chapter_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if state is None:
        state = workspace.load_state(work_id)
    for chapter_id, chapter_state in state.get("chapters", {}).items():
        if chapter_ids is not None and chapter_id not in chapter_ids:
            continue
        if chapter_state.get("skip"):
            continue
        reasons = _raw_validation_reasons(
            work_id,
            chapter_id,
            chapter_state,
            extraction_mode=state.get("extraction_mode") or "text",
        )
        if reasons:
            item = {
                "chapter_id": chapter_id,
                "title": chapter_state.get("title"),
                "pdf_pages": chapter_state.get("pdf_pages"),
                "reasons": reasons,
            }
            if chapter_state.get("failed_pages") is not None:
                item["failed_pages"] = _normalize_failed_pages(
                    chapter_state.get("failed_pages")
                )
            if chapter_state.get("error"):
                item["error"] = chapter_state.get("error")
            invalid.append(item)
    return invalid
```

- [x] **Step 5: Build prompt lists from the canonical projection**

Import `workspace` into `prompts.py`. In `prompts.build_prompts`, compute pending lists from the
supplied state, form a set union, then natural-sort that union. Return both new fields and the
compatibility union. Retain `skipped_chapter_ids` as all explicitly skipped chapters.

```python
pending = workspace.pending_chapters_from_state(state)
summary_pending = pending["summary_pending"]
extension_pending = pending["extension_pending"]
pending_ids = set(summary_pending) | set(extension_pending)
chapter_ids = [cid for cid in all_chapter_ids if cid in pending_ids]
```

Rewrite sequential and parallel workflow instructions so each worker checks membership in both canonical lists, fetches the chapter body once, and invokes only the matching save functions.

- [x] **Step 6: Validate exactly the pending union in the server**

Use the same loaded state snapshot before building the response:

```python
pending = workspace.pending_chapters_from_state(state)
pending_ids = set(pending["summary_pending"]) | set(pending["extension_pending"])
invalid = analysis.validate_chapter_raw_inputs(
    work_id,
    state,
    chapter_ids=pending_ids,
)
```

Change the success `next_action` to display both new lists and tell the caller to follow their result-specific actions. If both are empty, direct the caller to `finalize_study`.

- [x] **Step 7: Run prompt and server tests and verify GREEN**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_prompts.py tests/test_server.py -k 'subagent_prompts or pending_union or pending_raw or workflow_instructions'
```

Expected: PASS.

---

### Task 3: Make every workflow hint pending-aware

**Files:**
- Modify: `server.py:532-592, 947-1174, 1230-1244`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `workspace.pending_chapters_from_state(state)`
- Preserves: all MCP response envelopes and data fields outside the two additive prompt fields

- [x] **Step 1: Write failing guidance tests for asymmetric states**

Cover these transitions:

```python
# summary completed, extension pending
content = server.get_chapter_content(work_id, "ch1")
assert "save_extension_result" in content["next_action"]
assert "save_chapter_result" not in content["next_action"]

# extension completed, summary pending
content = server.get_chapter_content(work_id, "ch2")
assert "save_chapter_result" in content["next_action"]
assert "save_extension_result" not in content["next_action"]
```

After each save, assert the response recommends only the other still-pending kind or `list_pending_chapters`. Assert `resume_work` and `list_pending_chapters` render separate exact lists rather than a combined “do both” instruction.

- [x] **Step 2: Run the guidance tests and verify RED**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_server.py -k 'pending_guidance or resume_work'
```

Expected: FAIL because current `next_action` strings unconditionally mention both result generators.

- [x] **Step 3: Add one server-local pending guidance formatter**

Add a pure formatter that receives the state snapshot and optional chapter ID and returns instructions from the exact remaining actions:

```python
def _pending_kinds(state: dict[str, Any], chapter_id: str) -> list[str]:
    pending = workspace.pending_chapters_from_state(state)
    kinds: list[str] = []
    if chapter_id in pending["summary_pending"]:
        kinds.append("summary")
    if chapter_id in pending["extension_pending"]:
        kinds.append("extension")
    return kinds
```

Use this result after `get_chapter_content`, `save_chapter_result`, and `save_extension_result` to mention only `save_chapter_result`, only `save_extension_result`, both, or the final pending-list check. Do not introduce a new state write.

- [x] **Step 4: Remove duplicate extension filtering from server endpoints**

Use `workspace.pending_chapters_from_state(state)` directly in `resume_work`, `list_pending_chapters`, and `finalize_study`. Because the helper already honors `question_options.extension`, do not filter `extension_pending` a second time.

Ensure `resume_work` uses the state returned by `resume_workspace` and other endpoints use their already-loaded state so each response is internally consistent.

- [x] **Step 5: Run all server and workspace tests and verify GREEN**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_workspace.py tests/test_prompts.py tests/test_server.py tests/test_skip_chapter.py
```

Expected: PASS.

---

### Task 4: Align project documentation and close F-011

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `docs/architecture.md`
- Modify: `docs/business-rules.md`
- Modify: `docs/contracts.md`
- Modify: `docs/engineering-notes.md`
- Modify: `docs/standards.md`
- Modify: `docs/findings.md`

**Interfaces:**
- Documents: separate prompt pending lists, compatibility-union semantics, pending-only raw validation, and pending-aware workflow guidance

- [x] **Step 1: Update canonical behavior documents**

Record that `get_subagent_prompts` exposes `summary_pending_chapter_ids` and
`extension_pending_chapter_ids`; `chapter_ids` is their compatibility union; and completed
chapters are excluded from raw validation and processing guidance. Mirror the concise mandatory rule
in both `AGENTS.md` and `CLAUDE.md`.

- [x] **Step 2: Mark F-011 resolved**

Change its heading to `[해결: 2026-07-21]`, document the approved approach and verification evidence,
and update the recommended order so F-011 is complete without beginning F-005 or another finding.

- [x] **Step 3: Check documentation consistency**

Run:

```bash
rtk cmp -s AGENTS.md CLAUDE.md
rtk rg -n 'summary_pending_chapter_ids|extension_pending_chapter_ids|F-011' AGENTS.md CLAUDE.md docs
```

Expected: mirrored instructions match, the new contract appears in the canonical docs, and F-011 is
the only finding changed in substance.

---

### Task 5: Review, verify, and commit the completed item

**Files:**
- Review all F-011 implementation and documentation files
- Modify: `docs/tracking/status.md`
- Update checkboxes: `docs/superpowers/plans/2026-07-21-f011-pending-resume.md`
- Exclude: `docs/findings2.md`

**Interfaces:**
- Verifies the approved F-011 design and repository-wide compatibility

- [x] **Step 1: Review the complete diff**

Check for accidental contract changes, duplicated pending rules, status mutation in read paths,
completed-result regeneration guidance, and unrelated files. Correct only issues within F-011.

- [x] **Step 2: Run the full test suite**

Run:

```bash
rtk .venv/bin/pytest -q
```

Expected: all tests pass; only previously known dependency deprecation warnings may remain.

- [x] **Step 3: Run repository hygiene checks**

Run:

```bash
rtk git diff --check
rtk cmp -s AGENTS.md CLAUDE.md
rtk git status --short
```

Expected: no whitespace errors, mirrored instructions match, and `docs/findings2.md` remains the only
unrelated untracked file.

- [x] **Step 4: Record final verification evidence**

Update `docs/tracking/status.md` with the fresh full-suite count. Mark every executed plan checkbox
complete, including the verification steps whose evidence was just obtained.

- [x] **Step 5: Commit final verification records**

Stage only `docs/tracking/status.md` and the implementation plan, then commit with:

```bash
rtk git commit -m 'docs: F-011 검증 결과 기록'
```

- [x] **Step 6: Confirm the commit**

Run:

```bash
rtk git status --short
rtk git show -1 --oneline --stat
```

Expected: the implementation commit is present and the only remaining working-tree entry is the
untracked `docs/findings2.md`.
