# F-009 Managed Output Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Prevent a reused output directory from mixing previous workspace data, rendered formats, removed chapters, or incompatible progress with the current study.

**Architecture:** `init_work` detects an existing managed output before mutation and returns exact resume/replace/new-directory choices; replacement is explicit and clears only the previous `.work` while retaining the last rendered generation until a new render succeeds. A renderer output manager stages a complete generation, preserves progress only when the format and deterministic study fingerprint match, then installs the staged generation with rollback and records the managed paths in `.pdf-study-manifest.json`.

**Tech Stack:** Python 3.11+, pathlib, hashlib/json/tempfile/shutil from the standard library, FastMCP response envelopes, pytest.

## Global Constraints

- Do not change F-010 `work_id` generation or registry collision behavior.
- Keep every MCP response in `{ok, error, data, next_action}` form.
- Preserve the exact server-provided collision choice labels and descriptions.
- Never remove unrelated files from an output directory; cleanup is limited to `.work` during explicit replacement and paths recorded in a valid manifest during render installation.
- Ignore summary, quiz, and extension files whose current state status is not `completed`, including `force=true` renders.
- Preserve progress only when both output format and deterministic study fingerprint match.
- Use test-first red/green cycles for every behavior change.

---

### Task 1: Existing-output collision contract

**Files:**
- Modify: `workspace.py`
- Modify: `server.py`
- Test: `tests/test_workspace.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `workspace.inspect_output_dir(output_dir: str | Path) -> dict[str, Any]`
- Produces: `workspace.replace_workspace(output_dir: str | Path) -> None`
- Changes: `server.init_work(..., replace_existing: bool = False)`

- [x] **Step 1: Write failing workspace and server tests**

Add tests proving that an existing `.work/state.json` is detected, a normal repeated `init_work` leaves it byte-for-byte unchanged and returns the three structured choices, and `replace_existing=True` removes only `.work` while preserving unrelated and previously rendered files.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_workspace.py tests/test_server.py -k 'existing_output or replace_existing'`

Expected: FAIL because collision inspection, replacement, and the optional input do not exist.

- [x] **Step 3: Implement the minimal collision boundary**

Implement read-only collision inspection in `workspace.py`. In `server.init_work`, return `ok=false` before `make_work_id` or workspace mutation when an existing managed output is found and replacement was not explicitly requested. Return choices with values `resume`, `replace`, and `new_output_dir`; make `replace_existing=True` call the workspace replacement helper before creating the fresh state.

- [x] **Step 4: Run the focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest -q tests/test_workspace.py tests/test_server.py -k 'existing_output or replace_existing'`

Expected: PASS.

### Task 2: State-authoritative force rendering

**Files:**
- Modify: `renderer/html_renderer.py`
- Test: `tests/test_renderer.py`

**Interfaces:**
- Changes: `renderer.html_renderer._load_all(work_id)` reads result files only for completed status fields.

- [x] **Step 1: Write a failing stale-result regression test**

Create a work whose current chapter status is pending while same-ID legacy summary, quiz, and extension files exist, call `finalize_study(..., force=True)`, and assert the old content is absent from HTML.

- [x] **Step 2: Run the regression test and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_renderer.py -k 'force_ignores_stale_results'`

Expected: FAIL because `_load_all` reads files solely by existence.

- [x] **Step 3: Gate result loads by current state**

Load summary and quiz only when `summary_status == "completed"`; load extension only when `extension_status == "completed"`. Keep raw loading unchanged because it is not rendered as user-visible content.

- [x] **Step 4: Run the regression test and renderer suites**

Run: `.venv/bin/python -m pytest -q tests/test_renderer.py tests/test_md_tui_renderer.py`

Expected: PASS.

### Task 3: Managed staged rendering and compatible progress

**Files:**
- Create: `renderer/output_manager.py`
- Modify: `renderer/base.py`
- Modify: `renderer/html_renderer.py`
- Modify: `renderer/md_tui_renderer.py`
- Modify: `server.py`
- Test: `tests/test_renderer.py`
- Test: `tests/test_md_tui_renderer.py`

**Interfaces:**
- Produces: `render_study_fingerprint(work_id: str) -> str`
- Produces: `install_rendered_output(work_id: str, output_format: str, render: Callable[[Path], None]) -> dict[str, Any]`
- Changes: renderer implementations render only into the staging directory supplied by the manager.

- [x] **Step 1: Write failing managed-output tests**

Add tests for HTML chapter reduction removing stale `chN.html`, HTML-to-MD+TUI replacement removing HTML-managed paths, unrelated root files surviving, same-fingerprint/same-format progress surviving, changed-content progress resetting, and a renderer exception leaving the previous generation and manifest unchanged.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_renderer.py tests/test_md_tui_renderer.py -k 'managed_output or progress_fingerprint or render_rollback'`

Expected: FAIL because no manifest or staged installation exists.

- [x] **Step 3: Implement deterministic fingerprints and manifests**

Hash canonical JSON containing the resolved PDF identity, current non-skip chapter metadata, enabled question options, and completed summary/quiz/extension payloads. Validate manifest version and relative top-level managed paths before using it. Store `version`, `work_id`, `output_format`, `study_fingerprint`, and `managed_paths` in `.pdf-study-manifest.json` using atomic JSON replacement.

- [x] **Step 4: Implement staged installation with rollback**

Render into a temporary sibling directory. Copy compatible progress into staging only when old and new format/fingerprint match. Move only old manifest-managed paths into a backup, install staged top-level paths, atomically write the new manifest, and restore the backup plus old manifest if any step fails. Never enumerate unrelated output paths for deletion.

- [x] **Step 5: Route finalize through the output manager**

Keep renderers responsible for format generation. Make `finalize_study` call the output manager around the selected renderer so phase completion and `.work` cleanup happen only after successful installation.

- [x] **Step 6: Run renderer and server suites and verify GREEN**

Run: `.venv/bin/python -m pytest -q tests/test_renderer.py tests/test_md_tui_renderer.py tests/test_server.py tests/test_skip_chapter.py`

Expected: PASS.

### Task 4: Contracts, decisions, and completion record

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `docs/architecture.md`
- Modify: `docs/business-rules.md`
- Modify: `docs/contracts.md`
- Modify: `docs/engineering-notes.md`
- Modify: `docs/operations.md`
- Modify: `docs/security.md`
- Modify: `docs/standards.md`
- Modify: `docs/tracking/status.md`
- Modify: `docs/tracking/decisions/index.md`
- Create: `docs/tracking/decisions/0008-managed-output-replacement.md`
- Modify: `docs/findings.md`
- Modify: `renderer/AGENTS.md`
- Modify: `templates/AGENTS.md`

**Interfaces:**
- Documents: collision choice response, explicit replacement input, manifest ownership, staging/rollback, and progress compatibility.

- [x] **Step 1: Update all affected contracts and operating rules**

Describe exact choice values and the `replace_existing` opt-in; state that unrelated files are never managed, forced rendering follows status rather than file existence, and progress reuse requires matching format and fingerprint.

- [x] **Step 2: Record the decision and close only F-009**

Add decision 0008, mark F-009 resolved with implementation and verification notes, and update the recommended order so F-010 is the next item. Do not change any other finding status.

- [x] **Step 3: Verify document entry files remain synchronized**

Run: `cmp AGENTS.md CLAUDE.md`

Expected: exit 0.

### Task 5: Full verification and commit

**Files:**
- Verify all files changed by Tasks 1-4.

**Interfaces:**
- Produces: one F-009 commit without unrelated `docs/findings2.md`.

- [x] **Step 1: Run targeted tests**

Run: `.venv/bin/python -m pytest -q tests/test_workspace.py tests/test_server.py tests/test_renderer.py tests/test_md_tui_renderer.py tests/test_skip_chapter.py`

Expected: all pass with only the known SWIG deprecation warnings.

- [x] **Step 2: Run the complete test suite**

Run: `.venv/bin/python -m pytest -q`

Expected: 227 tests pass with only the known SWIG deprecation warnings; if the known stale fixture issue reproduces, rebuild only in an isolated temporary copy and report both results without changing F-005.

- [x] **Step 3: Review the final diff and status**

Confirm no unrelated untracked file is staged and no F-010 implementation is present.

- [x] **Step 4: Commit the completed item**

Create one conventional commit describing the prevention of mixed study outputs, then verify the working tree contains only pre-existing unrelated files.
