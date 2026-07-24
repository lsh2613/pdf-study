# Server-side answer placement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Have the generation agent supply a multiple-choice question's correct answer and distractors while the MCP server assigns and persists its one-time randomized option order.

**Architecture:** `question_contract.py` will accept and materialize the new agent-facing shape into the existing persisted `options` and `answer_index` shape. `server.save_chapter_result` invokes that materialization before its normal validation and atomic workspace write; the renderer remains a read-only consumer of the persisted canonical shape. Existing `options` and `answer_index` input remains accepted for compatibility.

**Tech Stack:** Python 3.10+, standard-library `random.SystemRandom`, pytest, existing MCP and renderer stack.

## Global Constraints

- The agent owns the question, one `correct_answer`, `incorrect_answers`, and explanation; the server owns option placement.
- Random placement is performed exactly once for a successful save; stored `options` and `answer_index` are never re-shuffled by rendering, resumption, or finalization.
- Existing `options` plus `answer_index` agent payloads remain accepted and retain their specified ordering.
- Persisted quiz JSON and renderer inputs remain `options` plus `answer_index`; the new agent-owned fields are never persisted.
- New-format validation failures use the existing `data.missing` error path and leave no chapter result files or completed status.
- Do not add dependencies or send PDF/question data outside the local process.
- Preserve unrelated user edits already present in the worktree; stage only files changed for this feature.
- All shell commands begin with `rtk`.

---

### Task 1: Define and test the agent-facing multiple-choice contract

**Files:**
- Modify: `question_contract.py:1-210`
- Modify: `prompts.py:120-160`
- Modify: `tests/test_question_contract.py`
- Modify: `tests/test_prompts.py`
- Modify: `docs/contracts.md:65`

**Interfaces:**
- Produces: `_shuffle_choices(choices: list[str]) -> None` and `materialize_multiple_choice_options(data: Any, *, shuffle_options: Callable[[list[str]], None] | None = None) -> tuple[Any, list[str]]`.
- Consumes: a summary payload where each multiple-choice item uses either the legacy `{options, answer_index}` shape or `{correct_answer, incorrect_answers}`.
- Produces: a copied canonical summary payload and new-format `data.missing` paths; canonical items contain only `id`, `question`, `options`, `answer_index`, and `explanation`.

- [ ] **Step 1: Add failing contract and prompt tests**

Add to `tests/test_question_contract.py`:

```python
def test_materialize_multiple_choice_places_correct_answer_after_server_shuffle():
    data = question_contract.summary_payload_example()
    item = data["questions"]["multiple_choice"][0]
    item.pop("options")
    item.pop("answer_index")
    item.update(
        question="질문",
        explanation="해설",
        correct_answer="정답",
        incorrect_answers=["오답 A", "오답 B"],
    )

    normalized, missing = question_contract.materialize_multiple_choice_options(
        data,
        shuffle_options=lambda values: values.reverse(),
    )

    assert missing == []
    assert normalized["questions"]["multiple_choice"][0] == {
        "id": "mc_1",
        "question": "질문",
        "options": ["오답 B", "오답 A", "정답"],
        "answer_index": 2,
        "explanation": "해설",
    }
    assert "correct_answer" not in data["questions"]["multiple_choice"][0]


def test_materialize_multiple_choice_rejects_missing_correct_answer():
    data = question_contract.summary_payload_example()
    item = data["questions"]["multiple_choice"][0]
    item.pop("options")
    item.pop("answer_index")
    item["incorrect_answers"] = ["오답"]

    _, missing = question_contract.materialize_multiple_choice_options(data)

    assert missing == ["questions.multiple_choice[0].correct_answer"]
```

Update `tests/test_prompts.py` to assert that the `summarizer_prompt` includes `correct_answer` and `incorrect_answers`, but no longer declares the agent responsible for `answer_index`.

- [ ] **Step 2: Verify RED**

Run: `rtk .venv/bin/python -m pytest -q tests/test_question_contract.py tests/test_prompts.py`

Expected: FAIL because `materialize_multiple_choice_options` does not exist and the existing prompt example still requires `options` and `answer_index`.

- [ ] **Step 3: Implement the contract and prompt change**

In `question_contract.py`, add `_shuffle_choices`, which calls `random.SystemRandom().shuffle(choices)`, and add the materializer. The materializer must deep-copy `data`, recognize new format when either `correct_answer` or `incorrect_answers` is present, and preserve legacy items untouched. For a valid new item, combine the correct answer and distractors, call `shuffle_options` when supplied or `_shuffle_choices` otherwise, find the correct answer's new index, and replace the item with:

```python
{
    "id": item["id"],
    "question": item["question"],
    "options": choices,
    "answer_index": choices.index(item["correct_answer"]),
    "explanation": item["explanation"],
}
```

Return `questions.multiple_choice[index].correct_answer` when the correct answer is missing or blank. Return `questions.multiple_choice[index].incorrect_answers` when it is not a nonempty string list, contains the correct answer, or contains duplicate choices. Leave malformed items unmaterialized so `missing_summary_fields` continues to report its existing field errors as well.

Keep `summary_payload_example()` as the canonical legacy/persisted fixture so current callers and test helpers remain unchanged. Add `agent_summary_payload_example()` for the rendered summarizer JSON example, using the agent format:

```python
{
    "id": "mc_1",
    "question": "질문",
    "correct_answer": "정답",
    "incorrect_answers": ["오답"],
    "explanation": "해설",
}
```

Add a direct prompt instruction: agent must not choose answer position or emit `options` / `answer_index` for multiple-choice questions; the server will assign the order when saving.

In the `save_chapter_result` contract documentation, describe both accepted input shapes and the canonical persisted shape, including one-time server-side placement and immutability after a successful save.

- [ ] **Step 4: Verify GREEN**

Run: `rtk .venv/bin/python -m pytest -q tests/test_question_contract.py tests/test_prompts.py`

Expected: PASS.

- [ ] **Step 5: Commit contract changes**

```bash
rtk git add question_contract.py prompts.py tests/test_question_contract.py tests/test_prompts.py docs/contracts.md
rtk git commit -m "feat: define server-side answer placement contract"
```

### Task 2: Materialize choices once at the save boundary

**Files:**
- Modify: `server.py:1123-1174`
- Modify: `tests/test_server.py`
- Modify: `docs/business-rules.md:52-60`

**Interfaces:**
- Consumes: `question_contract.materialize_multiple_choice_options(data_to_save)`.
- Produces: calls to `workspace.save_chapter_result` with canonical question JSON only.
- Preserves: `save_chapter_result` response envelope, chapter write rollback, state transitions, and legacy payload handling.

- [ ] **Step 1: Add failing save-boundary tests**

Add to `tests/test_server.py`:

```python
import json


def test_save_chapter_result_materializes_agent_choices_once(tmp_path, ko_short, monkeypatch):
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    result = _result()
    item = result["questions"]["multiple_choice"][0]
    item.pop("options")
    item.pop("answer_index")
    item.update(correct_answer="정답", incorrect_answers=["오답 A", "오답 B"])
    monkeypatch.setattr(
        question_contract,
        "_shuffle_choices",
        lambda values: values.reverse(),
    )

    assert server.save_chapter_result(wid, "ch1", result)["ok"] is True
    saved = json.loads((workspace.quiz_dir(wid) / "ch1.json").read_text(encoding="utf-8"))
    saved_item = saved["multiple_choice"][0]
    assert saved_item["options"] == ["오답 B", "오답 A", "정답"]
    assert saved_item["answer_index"] == 2
    assert "correct_answer" not in saved_item
    assert "incorrect_answers" not in saved_item


def test_save_chapter_result_rejects_invalid_agent_choice_payload_without_files(tmp_path, ko_short):
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    result = _result()
    item = result["questions"]["multiple_choice"][0]
    item.pop("options")
    item.pop("answer_index")
    item["correct_answer"] = "정답"
    item["incorrect_answers"] = ["정답"]

    response = server.save_chapter_result(wid, "ch1", result)

    assert response["ok"] is False
    assert response["data"]["missing"] == ["questions.multiple_choice[0].incorrect_answers"]
    _assert_no_chapter_result_files(wid, "ch1")
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "pending"
```

Add a third test using a legacy `_result()` unchanged and assert that its original `options` and `answer_index` are persisted untouched.

- [ ] **Step 2: Verify RED**

Run: `rtk .venv/bin/python -m pytest -q tests/test_server.py -k 'materializes_agent_choices_once or invalid_agent_choice_payload or legacy_choice_payload'`

Expected: the new-format success test fails validation with missing `options` and `answer_index`; the invalid-payload error lacks the new `incorrect_answers` path.

- [ ] **Step 3: Implement the one-time save transformation**

In `server.save_chapter_result`, after removing `body_text` and before `missing_summary_fields`, add:

```python
data_to_save, materialization_missing = question_contract.materialize_multiple_choice_options(
    data_to_save,
)
if materialization_missing:
    return _err(
        f"챕터 결과에 필수 값이 비었거나 누락됐습니다: {materialization_missing}.",
        data={"missing": materialization_missing, "chapter_id": chapter_id},
    )
```

Then keep the current canonical `missing_summary_fields` call and workspace save flow unchanged. Do not touch either renderer: both already consume the persisted canonical structure.

In `docs/business-rules.md`, add that agents supply the semantic answer/distractors for multiple-choice questions and the server assigns the option order once when the result is saved; stored position never changes later.

- [ ] **Step 4: Verify GREEN and regression behavior**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_server.py -k 'materializes_agent_choices_once or invalid_agent_choice_payload or legacy_choice_payload'
rtk .venv/bin/python -m pytest -q tests/test_server.py tests/test_question_contract.py tests/test_prompts.py tests/test_renderer.py tests/test_md_tui_renderer.py
```

Expected: all selected and related tests PASS. The saved-output assertions prove repeated reads/rendering use materialized stored order rather than shuffling again.

- [ ] **Step 5: Commit save-boundary changes**

```bash
rtk git add server.py tests/test_server.py docs/business-rules.md
rtk git commit -m "feat: randomize answer positions at save time"
```

### Task 3: Complete the repository regression check

**Files:**
- Modify: no additional files unless a test reveals a feature regression.

**Interfaces:**
- Verifies: existing MCP and renderer contracts still accept legacy questions and render canonical saved questions.

- [ ] **Step 1: Run the full test suite**

Run: `rtk .venv/bin/python -m pytest -q`

Expected: PASS with the feature tests and the existing suite.

- [ ] **Step 2: Inspect the scoped diff**

Run:

```bash
rtk git diff HEAD~2..HEAD -- question_contract.py prompts.py server.py tests/test_question_contract.py tests/test_prompts.py tests/test_server.py docs/contracts.md docs/business-rules.md
rtk git status --short
```

Expected: the two feature commits contain only the documented contract/save-boundary changes; pre-existing user changes remain unstaged and untouched.
