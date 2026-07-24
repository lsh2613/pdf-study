# Elicitation-Only Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove user-choice parameters and structured choice fallbacks from the public MCP contract so every user decision comes from form elicitation, while deriving output paths from the active Codex workspace.

**Architecture:** Keep synchronous business functions as internal implementation units, but expose only async MCP wrappers with reduced schemas. Each choice-bearing wrapper fails closed when the client lacks form elicitation, gathers user-owned values through `ctx.elicit`, validates them, and calls the sync implementation once. Operational identifiers and generated payloads remain ordinary MCP inputs.

**Tech Stack:** Python 3.11+, FastMCP/MCP Python SDK `>=1.28.0`, Pydantic form schemas, pytest.

## Global Constraints

- `output_dir` is always `<single request workspace>/result/<sanitized PDF stem>`.
- Server process cwd is never an output-path fallback.
- Missing or ambiguous workspace metadata fails without filesystem mutation.
- `user_context` is an optional form field; omission and blank input are valid.
- User-choice parameters are absent from public MCP input schemas.
- Choice-bearing tools fail closed with `required_capability="elicitation.form"`.
- Essential tools without user decisions remain available.
- Internal sync functions remain unregistered and continue to own validation and state changes.
- Existing managed-output deletion and replacement safety rules remain unchanged.

---

## File Structure

- `server.py`: Reduced MCP wrappers, capability gate, workspace-derived paths, Elicitation forms, and choice-free next steps.
- `processing_mode_contract.py`: Internal Elicitation choices and choice-free public next-step/error metadata.
- `tests/test_mcp_context.py`: Capability, workspace, optional context, conflict action, and real FastMCP behavior.
- `tests/test_mcp_config.py`: Exact public MCP input schemas.
- `tests/test_processing_mode_contract.py`: No public four-combination fallback.
- `tests/test_server.py`: Updated next-step and error envelope expectations.
- `docs/architecture.md`, `docs/business-rules.md`, `docs/contracts.md`, `docs/engineering-notes.md`, `docs/operations.md`, `docs/security.md`, `docs/standards.md`: Elicitation-only external and operational contract.
- `docs/tracking/decisions/0009-request-context-and-elicitation.md`, `docs/tracking/status.md`: Decision and implementation status.

### Task 1: Fail-Closed Capability Boundary

**Files:**
- Modify: `server.py`
- Test: `tests/test_mcp_context.py`

**Interfaces:**
- Produces: `_elicitation_required(ctx: Context) -> dict[str, Any] | None`
- Response data: `{"required_capability": "elicitation.form"}`

- [ ] **Step 1: Write the failing capability test**

Add:

```python
def test_choice_tools_fail_closed_without_elicitation(tmp_path, ko_short):
    ctx = _ElicitationContext(
        cwd=tmp_path,
        elicitation_supported=False,
    )
    calls = [
        server._mcp_init_work_tool(pdf_path=str(ko_short), ctx=ctx),
        server._mcp_resume_work_tool(pdf_path=str(ko_short), ctx=ctx),
        server._mcp_scan_pdf_tool(work_id="missing", ctx=ctx),
        server._mcp_prepare_ocr_tool(work_id="missing", ctx=ctx),
        server._mcp_set_chapters_tool(
            work_id="missing",
            chapters=[],
            ctx=ctx,
        ),
        server._mcp_finalize_study_tool(work_id="missing", ctx=ctx),
        server._mcp_cleanup_work_tool(work_id="missing", ctx=ctx),
    ]

    responses = [asyncio.run(call) for call in calls]

    assert all(response["ok"] is False for response in responses)
    assert all(
        response["data"] == {
            "required_capability": "elicitation.form",
        }
        for response in responses
    )
    assert ctx.messages == []
    assert not (tmp_path / "result").exists()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mcp_context.py::test_choice_tools_fail_closed_without_elicitation \
  -q
```

Expected: FAIL because current wrappers use legacy arguments or access missing work state.

- [ ] **Step 3: Add the common capability gate**

Add:

```python
def _elicitation_required(ctx: Context) -> dict[str, Any] | None:
    if _client_supports_elicitation(ctx):
        return None
    return _err(
        "이 도구는 사용자 선택을 직접 받기 위해 MCP form elicitation 지원이 필요합니다.",
        data={"required_capability": "elicitation.form"},
        next_action=None,
    )
```

At the first executable line of each choice-bearing MCP wrapper:

```python
capability_error = _elicitation_required(ctx)
if capability_error is not None:
    return capability_error
```

Remove the wrapper-level `if _client_supports_elicitation(ctx)` fallback branches. The Elicitation path becomes unconditional after the gate.

- [ ] **Step 4: Run the capability test and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mcp_context.py::test_choice_tools_fail_closed_without_elicitation \
  -q
```

Expected: PASS and no workspace files are created.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_mcp_context.py
git commit -m "fix: require elicitation for user choices"
```

### Task 2: Workspace-Derived `init_work` and Elicited Existing-Work Action

**Files:**
- Modify: `server.py`
- Test: `tests/test_mcp_config.py`
- Test: `tests/test_mcp_context.py`

**Interfaces:**
- Public MCP input: `init_work(pdf_path: str)`
- Public MCP input: `resume_work(pdf_path: str)`
- Produces: `_ExistingWorkActionSelection.action: str`
- Extends: `_elicit_question_setup(..., output_dir: str | None, include_user_context: bool) -> dict[str, Any] | None`

- [ ] **Step 1: Write failing public-schema tests**

Add an async schema collector:

```python
def _mcp_input_properties():
    tools = asyncio.run(server.mcp.list_tools())
    return {
        tool.name: set(tool.inputSchema["properties"])
        for tool in tools
    }
```

Assert:

```python
def test_init_and_resume_public_schemas_exclude_choice_and_path_parameters():
    properties = _mcp_input_properties()
    assert properties["init_work"] == {"pdf_path"}
    assert properties["resume_work"] == {"pdf_path"}
```

- [ ] **Step 2: Write failing init Elicitation tests**

Replace the existing init test response with:

```python
{
    "enable_short_answer": False,
    "enable_reflection": True,
    "enable_extension": False,
}
```

Assert that `output_dir_confirmed` is not requested, the result path is
`tmp_path / "result" / ko_short.stem`, and the message contains that absolute path.

Add an optional-context test:

```python
def test_mcp_init_work_allows_omitted_user_context(tmp_path, ko_short):
    ctx = _ElicitationContext(
        cwd=tmp_path,
        responses=[{
            "enable_short_answer": False,
            "enable_reflection": False,
            "enable_extension": False,
        }],
    )
    response = asyncio.run(
        server._mcp_init_work_tool(pdf_path=str(ko_short), ctx=ctx)
    )
    assert response["ok"] is True
    assert workspace.load_state(response["data"]["work_id"])["user_context"] == ""
```

Add the supplied-context counterpart using `"user_context": "입문자"` and assert it is stored.

- [ ] **Step 3: Write failing existing-work action tests**

Create a managed work in the derived output path, then call the MCP wrapper with responses:

```python
[{"action": "resume"}]
```

Assert the original work ID is returned without a second form.

For replace, use:

```python
[
    {"action": "replace"},
    {
        "enable_short_answer": False,
        "enable_reflection": False,
        "enable_extension": False,
    },
]
```

Assert a new work ID is created at the same fixed output path. Add an unmanaged-file collision test asserting failure, no Elicitation action choices, and no overwrite.

- [ ] **Step 4: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mcp_config.py::test_init_and_resume_public_schemas_exclude_choice_and_path_parameters \
  tests/test_mcp_context.py::test_mcp_init_work_allows_omitted_user_context \
  tests/test_mcp_context.py::test_mcp_init_work_uses_elicited_user_context \
  tests/test_mcp_context.py::test_mcp_init_work_elicits_resume_for_existing_work \
  tests/test_mcp_context.py::test_mcp_init_work_elicits_replace_for_existing_work \
  -q
```

Expected: FAIL because old public parameters and fallback choices still exist.

- [ ] **Step 5: Add primitive existing-work action schema**

Add:

```python
class _ExistingWorkActionSelection(BaseModel):
    action: str = Field(
        description="기존 출력 작업 처리 방식",
        json_schema_extra={"enum": ["resume", "replace"]},
    )
```

Build the allowed values from `existing["can_resume"]`; if it cannot resume, expose only `replace`. Validate the returned value server-side.

- [ ] **Step 6: Make optional learner context part of the setup form**

Extend `_elicit_question_setup`:

```python
fields["user_context"] = (
    str | None,
    Field(
        default=None,
        description="선택 사항: 학습 목적, 배경지식, 관심 분야, 현재 수준",
    ),
)
```

Keep `output_dir` only in the form message. Remove `output_dir_confirmed` from the schema and return handling. Normalize accepted context with:

```python
user_context = (selected.pop("user_context", None) or "").strip()
```

- [ ] **Step 7: Reduce and implement `init_work`**

Use this public wrapper signature:

```python
async def _mcp_init_work_tool(
    pdf_path: str,
    ctx: Context,
) -> dict[str, Any]:
```

Resolve the single request workspace with `_agent_cwd(ctx)` and compute the fixed path with `_resolve_output_dir("", pdf_path, agent_cwd=agent_cwd)`.

For `available`, elicit setup and call internal `init_work` with fixed values. For managed existing work, elicit `resume` or `replace`; call internal `resume_work` or continue with `replace_existing=True`. For unmanaged collision, return an error without a `choices` field.

- [ ] **Step 8: Reduce and implement `resume_work`**

Use:

```python
async def _mcp_resume_work_tool(
    pdf_path: str,
    ctx: Context,
) -> dict[str, Any]:
```

Derive the same fixed path, elicit confirmation, then call internal `resume_work(output_dir=resolved, _agent_cwd_path=agent_cwd)`.

- [ ] **Step 9: Run init/resume tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mcp_config.py \
  tests/test_mcp_context.py \
  -q
```

Expected: all tests in both files pass after updating old calls to the reduced schemas.

- [ ] **Step 10: Commit**

```bash
git add server.py tests/test_mcp_config.py tests/test_mcp_context.py
git commit -m "feat: derive workspaces and elicit setup"
```

### Task 3: Remove Remaining Choice Parameters

**Files:**
- Modify: `server.py`
- Test: `tests/test_mcp_config.py`
- Test: `tests/test_mcp_context.py`

**Interfaces:**
- `scan_pdf(work_id, scan_size=30, force_vision=False)`
- `prepare_ocr(work_id)`
- `set_chapters(work_id, chapters, book_info=None)`
- `finalize_study(work_id)`
- `cleanup_work(work_id)`

- [ ] **Step 1: Write failing exact-schema tests**

Assert:

```python
def test_choice_tools_expose_only_operational_parameters():
    properties = _mcp_input_properties()
    assert properties["scan_pdf"] == {"work_id", "scan_size", "force_vision"}
    assert properties["prepare_ocr"] == {"work_id"}
    assert properties["set_chapters"] == {"work_id", "chapters", "book_info"}
    assert properties["finalize_study"] == {"work_id"}
    assert properties["cleanup_work"] == {"work_id"}
```

- [ ] **Step 2: Update FastMCP round-trip calls to omit choices**

Change registered-tool calls to:

```python
await client.call_tool("prepare_ocr", {"work_id": "work-ocr"})
await client.call_tool(
    "set_chapters",
    {"work_id": work_id, "chapters": chapters},
)
await client.call_tool("finalize_study", {"work_id": "work-finalize"})
```

Keep callback responses as the only source of OCR language, extraction/execution modes, and output format. Assert captured sync calls contain those Elicitation values.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mcp_config.py::test_choice_tools_expose_only_operational_parameters \
  tests/test_mcp_context.py \
  -q
```

Expected: schema assertions fail because choice parameters are still public.

- [ ] **Step 4: Reduce wrapper signatures**

Implement:

```python
async def _mcp_scan_pdf_tool(
    work_id: str,
    ctx: Context,
    scan_size: int = 30,
    force_vision: bool = False,
) -> dict[str, Any]:
```

If question fields remain pending, elicit them with optional learner context and pass the accepted values internally.

Implement:

```python
async def _mcp_prepare_ocr_tool(work_id: str, ctx: Context) -> dict[str, Any]
```

Always elicit language.

Implement:

```python
async def _mcp_set_chapters_tool(
    work_id: str,
    chapters: list[dict[str, Any]],
    ctx: Context,
    book_info: dict[str, Any] | None = None,
) -> dict[str, Any]
```

Always run the three existing forms. Pass `ocr_language=""` internally so stored language is used.

Implement:

```python
async def _mcp_finalize_study_tool(
    work_id: str,
    ctx: Context,
) -> dict[str, Any]
```

Always elicit the format and call internal `finalize_study(..., keep_work_dir=True)`.

Keep `cleanup_work(work_id, ctx)` and make its Elicitation unconditional after the capability gate.

- [ ] **Step 5: Run MCP tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mcp_config.py \
  tests/test_mcp_context.py \
  -q
```

Expected: all tests pass and real FastMCP callbacks are the only choice source.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_mcp_config.py tests/test_mcp_context.py
git commit -m "feat: remove public choice parameters"
```

### Task 4: Remove Structured Choice Fallbacks

**Files:**
- Modify: `server.py`
- Modify: `processing_mode_contract.py`
- Test: `tests/test_processing_mode_contract.py`
- Test: `tests/test_server.py`
- Test: `tests/test_mcp_context.py`

**Interfaces:**
- `set_chapters_next_step(...)` returns tool and `required_parameters=["chapters"]`
- `prepare_ocr` next step has no required user parameter
- `finalize_study` next step has no required user parameter
- Public MCP responses contain no `user_choice_required`, `user_choice_instruction`, or choice fallback

- [ ] **Step 1: Write failing no-fallback tests**

Replace combined-choice assertions with:

```python
def test_set_chapters_next_step_requires_only_agent_generated_chapters():
    assert processing_mode_contract.set_chapters_next_step(None) == {
        "tool": "set_chapters",
        "required_parameters": ["chapters"],
    }
```

Add a recursive assertion helper:

```python
def _assert_no_choice_fallback(value):
    if isinstance(value, dict):
        assert "user_choice_required" not in value
        assert "user_choice_instruction" not in value
        for nested in value.values():
            _assert_no_choice_fallback(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_choice_fallback(nested)
```

Use it on MCP responses from init, scan, set-chapters cancellation, pending-list finalization, and invalid sync-mode recovery.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_processing_mode_contract.py \
  tests/test_server.py \
  tests/test_mcp_context.py \
  -q
```

Expected: FAIL on existing choice containers and four-combination next steps.

- [ ] **Step 3: Separate internal form choices from public next steps**

Keep `extraction_choices()` and `execution_choices()` for Elicitation messages. Change:

```python
def set_chapters_next_step(text_quality: str | None) -> dict[str, Any]:
    return {
        "tool": "set_chapters",
        "required_parameters": ["chapters"],
    }
```

Remove public `choices`, `execution_modes`, `extraction_modes`,
`forced_extraction_mode`, `user_choice_required`, and `user_choice_instruction` from invalid-mode data. Keep a concise internal-invariant error message.

- [ ] **Step 4: Make next-step helpers choice-free**

Return:

```python
def _prepare_ocr_next_step() -> dict[str, Any]:
    return {"tool": "prepare_ocr", "required_parameters": []}


def _finalize_next_step() -> dict[str, Any]:
    return {"tool": "finalize_study", "required_parameters": []}
```

Do not expose question setup, OCR language setup, output-format choices, or existing-output choices from MCP wrapper responses. Retain private helpers only as inputs to `ctx.elicit`.

- [ ] **Step 5: Update tool descriptions**

Remove instructions telling agents to show choices or supply removed parameters. Describe that the server opens the required forms during the tool call.

- [ ] **Step 6: Run server and contract tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_processing_mode_contract.py \
  tests/test_server.py \
  tests/test_mcp_context.py \
  -q
```

Expected: all tests pass with no choice fallback in MCP-facing responses.

- [ ] **Step 7: Commit**

```bash
git add \
  server.py \
  processing_mode_contract.py \
  tests/test_processing_mode_contract.py \
  tests/test_server.py \
  tests/test_mcp_context.py
git commit -m "refactor: remove structured choice fallbacks"
```

### Task 5: Documentation and Full Verification

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/architecture.md`
- Modify: `docs/business-rules.md`
- Modify: `docs/contracts.md`
- Modify: `docs/engineering-notes.md`
- Modify: `docs/operations.md`
- Modify: `docs/security.md`
- Modify: `docs/standards.md`
- Modify: `docs/tracking/decisions/0009-request-context-and-elicitation.md`
- Modify: `docs/tracking/status.md`

**Interfaces:**
- Documents exact public schemas and Elicitation-only behavior.
- Records the final full-suite test count.

- [ ] **Step 1: Update mandatory project rules**

Replace fallback instructions with:

```markdown
- 사용자 선택값은 MCP form elicitation 응답으로만 받는다. 선택 파라미터와 구조화
  fallback을 MCP 공개 계약에 다시 추가하면 안 된다. Elicitation 미지원 세션은 상태를
  바꾸지 않고 실패해야 한다.
- 출력 폴더는 요청의 단일 Codex workspace 또는 단일 MCP file root 아래
  `result/<pdf-name>`으로만 계산한다.
```

- [ ] **Step 2: Update architecture, contracts, and operations**

Document the exact public choice-bearing schemas:

```text
init_work(pdf_path)
resume_work(pdf_path)
scan_pdf(work_id, scan_size, force_vision)
prepare_ocr(work_id)
set_chapters(work_id, chapters, book_info)
finalize_study(work_id)
cleanup_work(work_id)
```

State that output paths are fixed, `user_context` is optional in the init form, unmanaged collisions fail, and Elicitation capability is mandatory.

- [ ] **Step 3: Update security, engineering notes, decision, and status**

Record that no agent-provided choice value is accepted at the MCP boundary, internal sync functions remain unregistered, and the server validates form enum values after receipt.

- [ ] **Step 4: Run documentation and schema checks**

Run:

```bash
git diff --check
.venv/bin/python -m pytest \
  tests/test_mcp_config.py \
  tests/test_mcp_context.py \
  tests/test_processing_mode_contract.py \
  tests/test_server.py \
  -q
```

Expected: whitespace check exits 0 and all focused tests pass.

- [ ] **Step 5: Run the complete suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass; the five known PyMuPDF/Paddle SWIG deprecation warnings may remain.

- [ ] **Step 6: Record the exact test count**

Update only the verified numeric count in `docs/tracking/status.md`.

- [ ] **Step 7: Commit documentation and verified status**

```bash
git add \
  AGENTS.md \
  docs/architecture.md \
  docs/business-rules.md \
  docs/contracts.md \
  docs/engineering-notes.md \
  docs/operations.md \
  docs/security.md \
  docs/standards.md \
  docs/tracking/decisions/0009-request-context-and-elicitation.md \
  docs/tracking/status.md
git commit -m "docs: require elicitation-only choices"
```

- [ ] **Step 8: Verify final repository state**

Run:

```bash
git diff --check
git status --short --branch
git log --oneline -8
```

Expected: no whitespace errors, a clean working tree, and the implementation commits after the approved design and plan commits.
