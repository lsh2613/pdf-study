# 단일 한국어 처리 흐름 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PDF 원문 언어와 무관하게 하나의 한국어 학습 자료 생성 흐름만 사용한다.

**Architecture:** 언어 감지와 작업 상태의 `language` 필드를 제거한다. `scan_pdf`, `set_chapters`, 프롬프트 생성, HTML 렌더링은 언어 인자나 분기 없이 기존 한국어 경로 하나만 사용한다.

**Tech Stack:** Python 3, MCP, PyMuPDF, PaddleOCR, pytest

## Global Constraints

- 챕터 경계 판단은 PDF 북마크 또는 목차 페이지 이미지에만 의존한다.
- `.work/state.json`은 `workspace.py` 헬퍼를 통해서만 갱신한다.
- 모든 PDF 원문 언어의 학습 자료는 한국어 프롬프트와 한국어 UI로 생성한다.
- 프롬프트 소스에는 영어 템플릿이나 영어 분기가 남지 않는다.

---

### Task 1: 언어 감지와 MCP 계약 제거

**Files:**
- Delete: `lang.py`, `tests/test_lang.py`
- Modify: `analysis.py`, `server.py`, `workspace.py`, `docs/contracts.md`, `docs/tracking/status.md`
- Test: `tests/test_analysis_e2e.py`, `tests/test_server.py`

- [ ] **Step 1: 실패하는 계약 테스트 작성**

`scan_pdf` 결과와 상태에 `language`가 없고, OCR `set_chapters`가 언어 인자 없이 실행됨을 검증한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_analysis_e2e.py tests/test_server.py -q`

Expected: 기존 언어 필드와 인자를 기대하는 테스트가 실패한다.

- [ ] **Step 3: 최소 구현**

`analysis.scan_pdf_impl`의 언어 감지와 상태/응답 필드를 제거하고, `set_chapters_impl`과 MCP 도구에서 `language` 인자를 없앤다. 초기 state와 계약 문서를 같은 계약으로 갱신한다.

- [ ] **Step 4: 대상 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_analysis_e2e.py tests/test_server.py -q`

Expected: PASS

### Task 2: 한국어 프롬프트 단일화

**Files:**
- Modify: `prompts.py`, `tests/test_prompts.py`

- [ ] **Step 1: 실패하는 프롬프트 테스트 작성**

어떤 state 값에서도 `build_prompts`가 하나의 한국어 프롬프트를 반환하고, `prompts.py`의 영어 템플릿/분기가 사라졌음을 검증한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_prompts.py -q`

Expected: 기존 영어 템플릿 기대 테스트가 실패한다.

- [ ] **Step 3: 최소 구현**

영어 템플릿, 영어 라벨, `language` 인자와 반환 필드를 제거한다. 한국어 프롬프트에 원문 언어와 무관하게 한국어 학습 자료를 작성하도록 명시한다.

- [ ] **Step 4: 대상 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_prompts.py -q`

Expected: PASS

### Task 3: 렌더러와 전체 회귀 검증

**Files:**
- Modify: `renderer/html_renderer.py`, `tests/test_renderer.py`
- Test: `tests/test_renderer.py`, `tests/test_md_tui_renderer.py`, 전체 테스트

- [ ] **Step 1: 실패하는 렌더러 테스트 작성**

HTML 문서가 상태 언어에 따라 바뀌지 않고 한국어 `lang` 속성을 사용함을 검증한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_renderer.py -q`

Expected: 기존 상태 언어 반영 테스트가 실패한다.

- [ ] **Step 3: 최소 구현**

상태에서 언어를 읽는 분기를 제거하고 HTML 셸에 고정 한국어 언어 코드를 전달한다.

- [ ] **Step 4: 전체 회귀 검증**

Run: `.venv/bin/python -m pytest -q`

Expected: PASS
