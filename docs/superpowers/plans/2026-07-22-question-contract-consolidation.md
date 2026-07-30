# 문제 JSON 계약 단일화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 문제 JSON의 검증 규칙과 예시를 Python 내부 모듈 한 곳에서 관리한다.

**Architecture:** `question_contract.py`가 저장 검증과 유효 예시의 유일한 코드 원본이 된다. `server.py`는 기존 MCP 저장 경계에서 이 모듈의 검증 함수를 호출하고, `prompts.py`와 테스트 helper는 같은 예시를 소비한다. 외부 저장 JSON과 오류 경로는 보존한다.

**Tech Stack:** Python 3.10+, 표준 라이브러리 `copy`·`json`, pytest.

## Global Constraints

- MCP 도구명·입력 JSON·응답 봉투·저장 JSON·상태 전환을 바꾸지 않는다.
- `data.missing`의 필드 경로와 현행 유효/무효 판정은 보존한다.
- JSON Schema·Pydantic·셸 검증·새 런타임 의존성을 추가하지 않는다.
- 문서 문자열 자체를 검증하는 테스트는 추가하지 않는다.
- 모든 셸 명령은 `rtk`로 시작하며, 사용자 소유 `docs/findings2.md`는 수정·stage하지 않는다.

---

### Task 1: Canonical contract module and direct validation tests

**Files:**
- Create: `question_contract.py`
- Create: `tests/test_question_contract.py`

**Interfaces:**
- Produces: `missing_summary_fields(data, options, chapter_id) -> list[str]`
- Produces: `missing_extension_fields(data, chapter_id) -> list[str]`
- Produces: `summary_payload_example() -> dict[str, Any]`
- Produces: `extension_payload_example() -> dict[str, Any]`

- [ ] **Step 1: Write failing contract tests**

Create `tests/test_question_contract.py` with direct examples of the required public internal API.

```python
from pdf_learner import question_contract


def test_summary_contract_reports_existing_missing_paths():
    data = question_contract.summary_payload_example()
    data["questions"]["multiple_choice"][0].pop("options")

    assert question_contract.missing_summary_fields(
        data,
        {"multiple_choice": True, "short_answer": True, "reflection": True},
        "ch1",
    ) == ["questions.multiple_choice[0].options"]


def test_extension_contract_reports_existing_missing_paths():
    data = question_contract.extension_payload_example()
    data["questions"]["extension"][0]["model_answer"] = ""

    assert question_contract.missing_extension_fields(data, "ch1") == [
        "questions.extension[0].model_answer"
    ]
```

- [ ] **Step 2: Verify RED**

Run: `rtk .venv/bin/python -m pytest tests/test_question_contract.py -q`

Expected: FAIL because `pdf_learner.question_contract` does not exist.

- [ ] **Step 3: Implement the contract module**

Create `question_contract.py`. Keep the current validation algorithm and field-path strings exactly, moving the relevant helpers from `server.py` without changing their semantics.

```python
BASIC_QUESTION_TYPES = ("multiple_choice", "short_answer", "reflection")


def summary_payload_example() -> dict[str, Any]:
    return {
        "summary": "요약",
        "key_points": ["핵심 포인트 1", "핵심 포인트 2"],
        "questions": {
            "multiple_choice": [{
                "id": "mc_1", "question": "...", "options": ["A", "B"],
                "answer_index": 0, "explanation": "...",
            }],
            "short_answer": [{"id": "sa_1", "question": "...", "model_answer": "..."}],
            "reflection": [{"id": "rf_1", "question": "...", "model_answer": "..."}],
        },
    }
```

Return a fresh object on every example call via `copy.deepcopy`; test mutations must not leak. Implement the current nonempty-string, non-boolean-integer, string-list, basic-item, summary and extension validation rules in this module.

- [ ] **Step 4: Verify GREEN**

Run: `rtk .venv/bin/python -m pytest tests/test_question_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated contract**

```bash
rtk git add question_contract.py tests/test_question_contract.py
rtk git commit -m "refactor: centralize question JSON contract"
```

### Task 2: Migrate server validation without changing its MCP behavior

**Files:**
- Modify: `server.py:220-350, 1180-1280`
- Modify: `tests/test_server.py:69-88` and existing invalid-save cases

**Interfaces:**
- Consumes: `question_contract.missing_summary_fields` and `question_contract.missing_extension_fields`.
- Preserves: save failure `data={"missing": [...], "chapter_id": chapter_id}` and existing state/file rollback behavior.

- [ ] **Step 1: Add a failing server-boundary delegation test**

Add a server test that replaces the contract validator with a deterministic probe. It proves the save tool delegates validation to the canonical module while leaving all normal invalid-input tests in place.

```python
def test_save_chapter_result_uses_question_contract(monkeypatch, ko_short, tmp_path):
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    monkeypatch.setattr(
        question_contract,
        "missing_summary_fields",
        lambda data, options, chapter_id: ["contract_probe"],
    )

    response = server.save_chapter_result(wid, "ch1", _result())

    assert response["ok"] is False
    assert response["data"]["missing"] == ["contract_probe"]
    assert response["data"]["chapter_id"] == "ch1"
```

- [ ] **Step 2: Verify RED**

Run: `rtk .venv/bin/python -m pytest tests/test_server.py -q -k uses_question_contract`

Expected: FAIL because `server.py` still uses its local validator and returns the normal validation result instead of `contract_probe`.

- [ ] **Step 3: Migrate `server.py`**

Import the contract module with the existing package-relative imports.

```python
from . import analysis, prompts, question_contract, workspace
```

Delete `_is_nonempty_str`, `_is_int`, `_validate_string_list`, `_validate_required_strings`, `_validate_basic_question_items`, `_missing_summary_fields`, and `_missing_extension_fields` from `server.py`. At the two save boundaries call:

```python
missing = question_contract.missing_summary_fields(data_to_save, options, chapter_id)
missing = question_contract.missing_extension_fields(data_to_save, chapter_id)
```

Keep `body_text` removal, `_ensure_save_target`, write order, failure response text, and state updates unchanged.

- [ ] **Step 4: Verify server behavior**

Run: `rtk .venv/bin/python -m pytest tests/test_server.py -q`

Expected: PASS with the same save success, invalid input, and rollback behavior.

- [ ] **Step 5: Commit the migration**

```bash
rtk git add server.py tests/test_server.py
rtk git commit -m "refactor: reuse question contract in save tools"
```

### Task 3: Reuse examples in prompts and rendering test fixtures

**Files:**
- Modify: `prompts.py:1-220`
- Modify: `tests/test_prompts.py`
- Modify: `tests/test_server.py:69-88`
- Modify: `tests/test_renderer.py:35-78`
- Modify: `tests/test_md_tui_renderer.py:23-60`

**Interfaces:**
- Consumes: fresh dicts returned by the two `question_contract` example functions.
- Preserves: prompt text requirements, disabled basic question keys as `[]`, and renderer test data shapes.

- [ ] **Step 1: Write failing prompt-delegation tests**

Add tests that monkeypatch each contract example function with a JSON-serializable probe and require the rendered prompt to contain that probe.

```python
def test_prompts_embed_canonical_question_examples(monkeypatch):
    monkeypatch.setattr(
        question_contract,
        "summary_payload_example",
        lambda: {"contract_probe": True},
    )
    prompt = prompts.build_prompts(_state())["summarizer_prompt"]
    assert '"contract_probe": true' in prompt
```

Add the equivalent extension probe test for `extension_payload_example`.

- [ ] **Step 2: Verify RED**

Run: `rtk .venv/bin/python -m pytest tests/test_prompts.py -q -k canonical_question_examples`

Expected: FAIL because the templates still carry handwritten JSON blocks rather than contract output.

- [ ] **Step 3: Render canonical examples and migrate fixtures**

Import `json` and `question_contract` in `prompts.py`. Replace handwritten JSON bodies in `_SUMMARIZER` and `_EXTENSION` with `{summary_json_example}` and `{extension_json_example}` placeholders. Pass pretty-printed JSON into `.format` using:

```python
summary_json_example=json.dumps(
    question_contract.summary_payload_example(), ensure_ascii=False, indent=2
)
```

Use `copy.deepcopy(question_contract.summary_payload_example())` and `copy.deepcopy(question_contract.extension_payload_example())` in the server and renderer test helper functions, then set only fixture-specific chapter IDs, summaries, question IDs, answer text, and option values. Do not handwrite a second complete question schema in those helpers.

- [ ] **Step 4: Verify focused suites**

Run:

`rtk .venv/bin/python -m pytest tests/test_question_contract.py tests/test_prompts.py tests/test_server.py tests/test_renderer.py tests/test_md_tui_renderer.py -q`

Expected: PASS.

- [ ] **Step 5: Commit prompt and fixture reuse**

```bash
rtk git add prompts.py tests/test_prompts.py tests/test_server.py tests/test_renderer.py tests/test_md_tui_renderer.py
rtk git commit -m "refactor: reuse question examples across prompts and tests"
```

### Task 4: Record the resolved finding and run the final regression

**Files:**
- Modify: `docs/findings.md`
- Modify: `docs/tracking/status.md`

**Interfaces:**
- Preserves: `docs/contracts.md` as human-readable external contract documentation; no documentation-string tests are introduced.

- [ ] **Step 1: Update the finding record**

Move the `문제 JSON 계약` row from the remaining-items table to `완료 기록`. State that `question_contract.py` is the code canonical source, server validation and prompt/fixture examples reuse it, and the external JSON and `data.missing` paths remain unchanged.

- [ ] **Step 2: Run final verification**

Run:

`rtk .venv/bin/python -m pytest -q`

`rtk .venv/bin/python -m py_compile question_contract.py server.py prompts.py`

`rtk git diff --check`

Expected: all tests pass, compilation succeeds, and no whitespace errors appear.

- [ ] **Step 3: Update the verified test count**

Set the count in `docs/tracking/status.md` to the actual total printed by the final pytest run. Do not infer it in advance.

- [ ] **Step 4: Commit the completion record**

```bash
rtk git add docs/findings.md docs/tracking/status.md
rtk git commit -m "docs: record question contract consolidation"
```
