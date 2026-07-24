# Split Processing Elicitation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single four-combination `set_chapters` form with three ordered MCP form elicitations for chapter confirmation, text/OCR extraction, and sequential/parallel execution.

**Architecture:** Keep the public `set_chapters` tool and its synchronous implementation unchanged. Add separate canonical choice projections in `processing_mode_contract.py`, split the MCP-only elicitation helper in `server.py`, and call the synchronous implementation only after all three forms are accepted. Preserve the existing four-combination structured fallback for clients without elicitation support.

**Tech Stack:** Python 3.11+, MCP Python SDK/FastMCP `>=1.28.0`, Pydantic dynamic models, pytest.

## Global Constraints

- The request order is chapter configuration/range confirmation, extraction mode, then execution mode.
- No `.work/state.json` mutation may occur until all three elicitation responses are accepted.
- `garbled` and `no_text_layer` PDFs must expose only `ocr` in the extraction form.
- Agent-provided `extraction_mode` and `execution_mode` values must be replaced by elicited values.
- The non-elicitation `data.next_step.choices` and error fallback keep the existing four mode combinations.
- Existing `set_chapters` input parameters and response envelope remain compatible.

---

## File Structure

- `processing_mode_contract.py`: Own combined compatibility choices and the new independent extraction/execution choice definitions.
- `server.py`: Own the three MCP-only elicitation requests and their ordered orchestration.
- `tests/test_processing_mode_contract.py`: Verify separated choices, OCR filtering, copy safety, and unchanged combined fallback.
- `tests/test_mcp_context.py`: Verify request order, argument override, cancellation safety, forced OCR, and FastMCP round trips.
- `docs/architecture.md`: Describe the three-stage server wrapper.
- `docs/business-rules.md`: State that extraction and execution are independent user decisions.
- `docs/contracts.md`: Define the exact elicitation order and compatibility behavior.
- `docs/engineering-notes.md`: Record the split form behavior for maintainers.
- `docs/tracking/decisions/0009-request-context-and-elicitation.md`: Extend the accepted decision.
- `docs/tracking/status.md`: Reflect the implemented behavior and final test count.

### Task 1: Independent Processing Choice Contract

**Files:**
- Modify: `processing_mode_contract.py`
- Test: `tests/test_processing_mode_contract.py`

**Interfaces:**
- Produces: `extraction_choices(text_quality: str | None) -> list[dict[str, str]]`
- Produces: `execution_choices() -> list[dict[str, str]]`
- Preserves: `choices(text_quality: str | None) -> list[dict[str, str]]`

- [ ] **Step 1: Write the failing independent-choice tests**

Add:

```python
def test_elicitation_choices_split_extraction_from_execution():
    assert processing_mode_contract.extraction_choices(None) == [
        {
            "value": "text",
            "label": "Text",
            "desc": "PDF 텍스트 레이어를 사용해 본문을 추출합니다.",
        },
        {
            "value": "ocr",
            "label": "OCR",
            "desc": "PaddleOCR CPU로 본문을 먼저 읽어 텍스트로 저장합니다.",
        },
    ]
    assert processing_mode_contract.execution_choices() == [
        {
            "value": "sequential",
            "label": "Sequential",
            "desc": "챕터를 한 개씩 순서대로 처리합니다.",
        },
        {
            "value": "parallel",
            "label": "Parallel",
            "desc": "최대 5개 sub-agent가 챕터를 동시에 처리합니다.",
        },
    ]


def test_elicitation_extraction_choices_force_ocr_without_changing_fallback():
    assert processing_mode_contract.extraction_choices("garbled") == [
        {
            "value": "ocr",
            "label": "OCR",
            "desc": "PaddleOCR CPU로 본문을 먼저 읽어 텍스트로 저장합니다.",
        },
    ]
    assert len(processing_mode_contract.choices("garbled")) == 2
    assert {
        choice["execution_mode"]
        for choice in processing_mode_contract.choices("garbled")
    } == {"sequential", "parallel"}
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_processing_mode_contract.py -q
```

Expected: FAIL because `extraction_choices` and `execution_choices` do not exist.

- [ ] **Step 3: Add minimal canonical independent choices**

Add immutable specs and fresh-copy helpers:

```python
_EXTRACTION_SPECS = (
    {
        "value": "text",
        "label": "Text",
        "desc": "PDF 텍스트 레이어를 사용해 본문을 추출합니다.",
    },
    {
        "value": "ocr",
        "label": "OCR",
        "desc": "PaddleOCR CPU로 본문을 먼저 읽어 텍스트로 저장합니다.",
    },
)

_EXECUTION_SPECS = (
    {
        "value": "sequential",
        "label": "Sequential",
        "desc": "챕터를 한 개씩 순서대로 처리합니다.",
    },
    {
        "value": "parallel",
        "label": "Parallel",
        "desc": "최대 5개 sub-agent가 챕터를 동시에 처리합니다.",
    },
)


def extraction_choices(text_quality: str | None) -> list[dict[str, str]]:
    specs = _EXTRACTION_SPECS
    if text_extraction_is_unavailable(text_quality):
        specs = tuple(spec for spec in specs if spec["value"] == "ocr")
    return [dict(spec) for spec in specs]


def execution_choices() -> list[dict[str, str]]:
    return [dict(spec) for spec in _EXECUTION_SPECS]
```

- [ ] **Step 4: Run contract tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_processing_mode_contract.py -q
```

Expected: all tests in the file pass and existing four-combination assertions remain unchanged.

- [ ] **Step 5: Commit the contract unit**

```bash
git add processing_mode_contract.py tests/test_processing_mode_contract.py
git commit -m "refactor: split processing choice definitions"
```

### Task 2: Three Ordered `set_chapters` Elicitations

**Files:**
- Modify: `server.py:637-699`
- Modify: `server.py:1517-1567`
- Test: `tests/test_mcp_context.py:13-38`
- Test: `tests/test_mcp_context.py:231-318`

**Interfaces:**
- Consumes: `processing_mode_contract.extraction_choices(text_quality)`
- Consumes: `processing_mode_contract.execution_choices()`
- Produces: `_elicit_chapter_setup(ctx, work_id, chapters) -> dict[str, Any] | None`
- Produces: `_elicit_extraction_mode(ctx, work_id) -> str | None`
- Produces: `_elicit_execution_mode(ctx) -> str | None`

- [ ] **Step 1: Let the test context represent decline and cancel**

Change the fake client to consume `_action` without constructing schema data:

```python
async def elicit(self, message, schema):
    self.messages.append(message)
    response = dict(self._responses.pop(0))
    action = response.pop("_action", "accept")
    if action != "accept":
        return SimpleNamespace(action=action, data=None)
    data = schema(**response)
    return SimpleNamespace(action=action, data=data)
```

- [ ] **Step 2: Rewrite the main mode test for three responses**

Use:

```python
ctx = _ElicitationContext(responses=[
    {
        "chapter_strategy": "proceed",
        "chapters_confirmed": True,
    },
    {"extraction_mode": "ocr"},
    {"execution_mode": "parallel"},
])
```

Pass agent arguments `execution_mode="sequential"` and `extraction_mode="text"`. Assert the saved state is `parallel/ocr`, `len(ctx.messages) == 3`, and:

```python
assert "[챕터 구성과 범위]" in ctx.messages[0]
assert "[본문 추출 방식]" not in ctx.messages[0]
assert "[본문 추출 방식]" in ctx.messages[1]
assert "Text" in ctx.messages[1]
assert "OCR" in ctx.messages[1]
assert "Sequential" not in ctx.messages[1]
assert "[실행 방식]" in ctx.messages[2]
assert "Sequential" in ctx.messages[2]
assert "Parallel" in ctx.messages[2]
assert "OCR" not in ctx.messages[2]
```

- [ ] **Step 3: Add cancellation tests for all three boundaries**

Parameterize response lists and expected request counts:

```python
@pytest.mark.parametrize(
    ("responses", "message_count"),
    [
        ([{"_action": "cancel"}], 1),
        (
            [
                {"chapter_strategy": "proceed", "chapters_confirmed": True},
                {"_action": "decline"},
            ],
            2,
        ),
        (
            [
                {"chapter_strategy": "proceed", "chapters_confirmed": True},
                {"extraction_mode": "text"},
                {"_action": "cancel"},
            ],
            3,
        ),
    ],
)
def test_mcp_set_chapters_cancellation_never_changes_state(
    responses, message_count, tmp_path, ko_short,
):
    initialized = server.init_work(
        str(ko_short),
        str(tmp_path / "out"),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    work_id = initialized["data"]["work_id"]
    scanned = server.scan_pdf(work_id)
    ctx = _ElicitationContext(responses=responses)

    response = asyncio.run(
        server._mcp_set_chapters_tool(
            work_id=work_id,
            chapters=scanned["data"]["recommendations"]["suggested_chapters"],
            execution_mode="parallel",
            extraction_mode="text",
            ctx=ctx,
        )
    )

    assert response["ok"] is False
    assert len(ctx.messages) == message_count
    assert workspace.load_state(work_id)["phases"]["chapter_setup"] != "completed"
```

- [ ] **Step 4: Add the forced-OCR form test**

Add `import pytest`, then construct the work and invoke only the extraction helper:

```python
initialized = server.init_work(
    str(ko_short),
    str(tmp_path / "ocr-only"),
    enable_short_answer=False,
    enable_reflection=False,
    enable_extension=False,
)
work_id = initialized["data"]["work_id"]
workspace.update_state(work_id, text_quality="garbled")
ctx = _ElicitationContext(responses=[{"extraction_mode": "ocr"}])

selected = asyncio.run(server._elicit_extraction_mode(ctx, work_id))
assert selected == "ocr"
assert "OCR" in ctx.messages[0]
assert "PDF 텍스트 레이어를 사용" not in ctx.messages[0]
```

Prove the schema rejects text rather than merely hiding it:

```python
invalid_ctx = _ElicitationContext(responses=[{"extraction_mode": "text"}])
with pytest.raises(ValueError):
    asyncio.run(server._elicit_extraction_mode(invalid_ctx, work_id))
```

- [ ] **Step 5: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mcp_context.py::test_mcp_set_chapters_uses_elicited_mode_and_confirms_chapters \
  tests/test_mcp_context.py::test_mcp_set_chapters_cancellation_never_changes_state \
  tests/test_mcp_context.py::test_mcp_extraction_elicitation_forces_ocr_for_garbled_text \
  -q
```

Expected: FAIL because the server still requests `processing_mode` in one form.

- [ ] **Step 6: Split the combined helper into three helpers**

Implement `_elicit_chapter_setup` by moving only chapter choices, rendered chapter ranges, and `chapters_confirmed` from `_elicit_processing_setup`.

Implement extraction mode with a dynamic Literal:

```python
async def _elicit_extraction_mode(ctx: Context, work_id: str) -> str | None:
    text_quality = workspace.load_state(work_id).get("text_quality")
    choices = processing_mode_contract.extraction_choices(text_quality)
    mode_type = Literal.__getitem__(tuple(choice["value"] for choice in choices))
    schema = create_model(
        "PdfStudyExtractionModeSelection",
        extraction_mode=(
            mode_type,
            Field(description="사용자가 선택한 본문 추출 방식"),
        ),
    )
    message = (
        "[본문 추출 방식]\n"
        + _choice_lines(choices)
        + "\nOCR 본문 선처리는 실행 방식과 별개의 서버 내부 상한으로 제한됩니다."
    )
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    return str(result.data.extraction_mode)
```

Implement execution mode independently:

```python
async def _elicit_execution_mode(ctx: Context) -> str | None:
    choices = processing_mode_contract.execution_choices()
    mode_type = Literal.__getitem__(tuple(choice["value"] for choice in choices))
    schema = create_model(
        "PdfStudyExecutionModeSelection",
        execution_mode=(
            mode_type,
            Field(description="사용자가 선택한 챕터 실행 방식"),
        ),
    )
    message = "[실행 방식]\n" + _choice_lines(choices)
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    return str(result.data.execution_mode)
```

- [ ] **Step 7: Orchestrate the helpers before the synchronous call**

Replace the combined call with:

```python
chapter_selection = await _elicit_chapter_setup(ctx, work_id, chapters)
if chapter_selection is None:
    return _elicitation_cancelled(...)
if chapter_selection.get("chapter_strategy") == "reanalyze_with_vision":
    return _err(...)
if not chapter_selection["chapters_confirmed"]:
    return _elicitation_cancelled(...)

selected_extraction_mode = await _elicit_extraction_mode(ctx, work_id)
if selected_extraction_mode is None:
    return _elicitation_cancelled(...)

selected_execution_mode = await _elicit_execution_mode(ctx)
if selected_execution_mode is None:
    return _elicitation_cancelled(...)

extraction_mode = selected_extraction_mode
execution_mode = selected_execution_mode
```

Keep the final `return set_chapters(...)` unchanged.

- [ ] **Step 8: Update the reanalysis test**

Remove `processing_mode` from its only response and assert `len(ctx.messages) == 1`, proving later forms are not shown after reanalysis.

- [ ] **Step 9: Run all MCP context tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_mcp_context.py -q
```

Expected: all tests pass.

- [ ] **Step 10: Commit the server behavior**

```bash
git add server.py tests/test_mcp_context.py
git commit -m "feat: split set chapters elicitations"
```

### Task 3: FastMCP Integration and Contract Documentation

**Files:**
- Modify: `tests/test_mcp_context.py`
- Modify: `docs/architecture.md`
- Modify: `docs/business-rules.md`
- Modify: `docs/contracts.md`
- Modify: `docs/engineering-notes.md`
- Modify: `docs/tracking/decisions/0009-request-context-and-elicitation.md`
- Modify: `docs/tracking/status.md`

**Interfaces:**
- Consumes: registered MCP tool `set_chapters`
- Verifies: three `elicitation/create` requests in chapter/extraction/execution order
- Documents: elicitation-supported split and unchanged non-elicitation fallback

- [ ] **Step 1: Add a FastMCP round-trip test for three forms**

Use a callback that responds by message heading:

```python
async def on_elicit(context, params):
    messages.append(params.message)
    if "[챕터 구성과 범위]" in params.message:
        content = {
            "chapter_strategy": "proceed",
            "chapters_confirmed": True,
        }
    elif "[본문 추출 방식]" in params.message:
        content = {"extraction_mode": "text"}
    elif "[실행 방식]" in params.message:
        content = {"execution_mode": "parallel"}
    else:
        raise AssertionError(params.message)
    return types.ElicitResult(action="accept", content=content)
```

Create and scan a work with the synchronous helpers, then call the registered tool:

```python
initialized = server.init_work(
    str(ko_short),
    str(tmp_path / "round-trip"),
    enable_short_answer=False,
    enable_reflection=False,
    enable_extension=False,
)
work_id = initialized["data"]["work_id"]
scanned = server.scan_pdf(work_id)

async def scenario():
    async with create_connected_server_and_client_session(
        server.mcp,
        elicitation_callback=on_elicit,
    ) as client:
        return await client.call_tool(
            "set_chapters",
            {
                "work_id": work_id,
                "chapters": scanned["data"]["recommendations"]["suggested_chapters"],
                "execution_mode": "sequential",
                "extraction_mode": "ocr",
            },
        )

result = asyncio.run(scenario())
assert result.structuredContent["ok"] is True
assert len(messages) == 3
assert "[챕터 구성과 범위]" in messages[0]
assert "[본문 추출 방식]" in messages[1]
assert "[실행 방식]" in messages[2]
```

- [ ] **Step 2: Run the integration test**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mcp_context.py::test_fastmcp_set_chapters_uses_three_ordered_elicitations \
  -q
```

Expected: PASS after Task 2.

- [ ] **Step 3: Update the external contract**

In `docs/contracts.md`, state:

```markdown
`set_chapters`의 form elicitation은 한 도구 호출 안에서 세 번 실행한다. 첫 번째는
챕터 구성 전략과 제목·PDF 범위를 확인하고, 두 번째는 text/OCR 본문 추출 방식을
선택하며, 세 번째는 sequential/parallel 실행 방식을 선택한다. 앞 단계가 거절·취소되면
뒤 요청과 상태 변경을 실행하지 않는다. 미지원 클라이언트의 구조화 fallback은 기존
네 조합을 유지한다.
```

- [ ] **Step 4: Update internal rules and decision records**

Make these exact documentation changes:

- `docs/architecture.md`: replace the single chapter/mode elicitation description with
  the ordered chapter, extraction, execution requests and the one final sync call.
- `docs/business-rules.md`: state that text/OCR and sequential/parallel are independent
  decisions and that cancellation at any request prevents state mutation.
- `docs/engineering-notes.md`: record the three message headings and explain that accepted
  earlier answers remain in memory only until all three requests succeed.
- `docs/tracking/decisions/0009-request-context-and-elicitation.md`: extend the decision
  bullet for chapter setup to require three independent forms in the fixed order.
- `docs/tracking/status.md`: replace the combined chapter/mode wording with the three-form
  behavior without changing the test count until the final suite has run.

- [ ] **Step 5: Run documentation and focused regression checks**

Run:

```bash
git diff --check
.venv/bin/python -m pytest \
  tests/test_processing_mode_contract.py \
  tests/test_mcp_context.py \
  tests/test_server.py \
  -q
```

Expected: `git diff --check` exits 0 and all focused tests pass.

- [ ] **Step 6: Commit integration and documentation**

```bash
git add \
  tests/test_mcp_context.py \
  docs/architecture.md \
  docs/business-rules.md \
  docs/contracts.md \
  docs/engineering-notes.md \
  docs/tracking/decisions/0009-request-context-and-elicitation.md \
  docs/tracking/status.md
git commit -m "docs: define staged processing elicitations"
```

### Task 4: Full Verification

**Files:**
- Modify: `docs/tracking/status.md`

**Interfaces:**
- Verifies the complete repository after all behavior and documentation changes.

- [ ] **Step 1: Run the complete test suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass; the known PyMuPDF/Paddle SWIG deprecation warnings may remain.

- [ ] **Step 2: Record the exact test count**

If the passing count changed, update only the numeric count in
`docs/tracking/status.md` to the result from Step 1.

- [ ] **Step 3: Run final repository checks**

Run:

```bash
git diff --check
git status --short --branch
```

Expected: no whitespace errors; only the intentional status-doc count change may be
uncommitted.

- [ ] **Step 4: Commit the verified status if it changed**

```bash
git add docs/tracking/status.md
git commit -m "docs: refresh verified test count"
```

- [ ] **Step 5: Confirm final history and clean tree**

Run:

```bash
git log --oneline -5
git status --short --branch
```

Expected: implementation commits follow the design commit and the working tree is clean.
