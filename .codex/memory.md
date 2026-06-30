# Codex Memory

Last updated: 2026-06-30 Asia/Seoul

## Repository State

- Repo: `/Users/seowon/Desktop/github/pdf-study`
- Current branch: `main`
- Remote: `origin git@github.com:lsh2613/pdf-study.git`
- `main`, `origin/main`, and `dryforge/paddleocr-cpu` all point to:
  - `77694b6 Enforce OCR preprocessing failure contract`
- Working tree was clean after merge and push:
  - `git status --short --branch` showed `## main...origin/main`
- The feature branch `dryforge/paddleocr-cpu` was fast-forward merged into `main` and pushed to `origin/main`.

## User Goal Completed

The user wanted OCR inside this MCP server to use PaddleOCR CPU mode instead of AI vision OCR anywhere OCR is needed.

Completed scope:

- `scan_pdf`:
  - If there is no outline, or `force_vision=True`, table-of-contents pages are rendered as images.
  - The server runs PaddleOCR CPU and returns `toc_page_images[].ocr_text` / `ocr_error`.
  - The server still does not infer chapter boundaries from PDF text layers.
- `set_chapters(..., extraction_mode="ocr")`:
  - Non-skip chapter pages are rendered and OCRed before any sub-agent prompt flow.
  - Raw chapter text is saved in `chapters_raw/chN.json` with `text`, `char_count`, and `extraction_mode="ocr"`.
  - Sub-agents no longer need to vision page images in OCR mode.
  - Chapter OCR parallelism is by chapter, not by page.
  - The global chapter OCR executor limits concurrent chapter OCR tasks process-wide:
    - CPU count `None`, `0`, or `1` -> 1
    - otherwise -> 2
  - Cached raw text is reused only if it is a valid OCR raw for the same `page_range`.
  - Legacy/no-mode raw and text-mode raw are not promoted to OCR cache.
  - If page OCR fails, or the whole chapter OCR result is blank:
    - no partial raw is saved
    - chapter status is failed
    - failed pages are recorded
    - server `set_chapters` returns `ok=false`, `next_action=null`, `data.failed_chapters`
- `get_subagent_prompts` / `get_chapter_content`:
  - Still require `chapter_raw.text` and exact `char_count` for non-skip chapters.
  - Failed OCR chapters are surfaced through `failed_chapters`.
- `save_chapter_result`:
  - `body_text` is no longer required.
  - If `body_text` is submitted for backward compatibility, it is stripped before summary save and does not overwrite canonical raw text.

## Key Files Touched

- `pdf/ocr.py`
  - PaddleOCR CPU boundary.
  - Project-local cache default: `.paddleocr`.
  - Env override: `PDF_STUDY_PADDLEOCR_CACHE`.
  - Sets Paddle cache envs including `PADDLEOCR_HOME` and `PADDLE_PDX_CACHE_HOME`.
  - Provides global chapter OCR executor via `submit_chapter_ocr`.
- `analysis.py`
  - TOC OCR attachment in `scan_pdf_impl`.
  - OCR-mode raw precompute and failure handling in `set_chapters_impl`.
  - Raw validation helpers.
- `server.py`
  - MCP envelope behavior for OCR preprocessing failure.
  - `body_text` no longer required.
  - User-facing mode descriptions now separate OCR precompute from sub-agent `execution_mode`.
- `workspace.py`
  - Raw/result persistence keeps canonical raw text protected.
- `prompts.py`
  - OCR mode prompt expects precomputed text, not page images or vision.
- Docs:
  - `docs/contracts.md`
  - `docs/business-rules.md`
  - `docs/standards.md`
  - `docs/engineering-notes.md`
  - `docs/tracking/status.md`
  - `docs/tracking/findings.md`
  - `docs/tracking/decisions/0003-text-or-ocr.md`
- Tests:
  - `tests/test_analysis_e2e.py`
  - `tests/test_server.py`
  - `tests/test_ocr.py`
  - `tests/test_prompts.py`
  - `tests/test_recommendations.py`
  - `tests/test_renderer.py`
  - `tests/test_setup_mcp.py`

## Verification

After merge to `main`, the full suite was run:

```bash
uv run --no-project --with pytest --with pymupdf --with pillow --with-editable . python -m pytest -q
```

Result:

- `183 passed`
- `5 warnings`
- Warnings are PyMuPDF/Paddle SWIG `DeprecationWarning`s.

## Review

The user requested:

- implementation with `gpt-5.5 high`
- review with `gpt-5.5 xhigh`

Final review was performed by a `gpt-5.5 xhigh` subagent.

Initial findings were fixed:

- Legacy/no-mode raw could be promoted to OCR cache after retry.
- Chapter OCR parallel limit was not process-global.
- OCR mode user-choice text conflated OCR precompute concurrency with sub-agent execution mode.

The reviewer rechecked latest HEAD `77694b6` and reported no remaining findings.

## Important Project Rules To Preserve

- Do not treat PDF learning requests as generic PDF summaries.
- Required workflow remains:
  - `init_work -> scan_pdf -> set_chapters -> get_subagent_prompts -> save_* -> list_pending_chapters -> finalize_study`
- Chapter boundaries must come only from:
  - PDF outline/bookmarks
  - table-of-contents page images
- Do not add PDF text-layer TOC guessing.
- `state.json` must be changed through locked `workspace.py` helpers.
- Skip chapters are excluded from raw extraction, sub-agent dispatch, and rendering.
- `body_text` is optional, but `chapter_raw.text` and exact `chapter_raw.char_count` are mandatory for non-skip chapters.

## Remaining Notes

- No known blocker remains.
- Runtime PaddleOCR model downloads/cache may still affect first-run latency.
- The full test suite stubs PaddleOCR where needed and should not load real models during tests.
- `.paddleocr`, `.work`, result outputs, `.venv`, and pytest caches should not be committed.
