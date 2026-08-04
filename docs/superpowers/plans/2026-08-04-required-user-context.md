# Required Learner Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 새 `init_work`가 비어 있지 않은 학습자 정보를 반드시 Elicitation으로 받게 하고, 그 정보가 기본·확장 문제 생성 지침에 실제로 전달되는 계약을 검증한다.

**Architecture:** 기존 선택형 문제 Elicitation 흐름은 유지하고, 공유 헬퍼에 `init_work` 전용 필수 모드를 추가한다. `scan_pdf`의 기존 작업 보완 흐름은 호환성을 위해 선택 입력을 유지하며, 공개 MCP 인자나 fallback은 추가하지 않는다. 필수 입력은 Pydantic form schema와 서버 측 trim 후 검증을 함께 적용해 공백만 제출하는 경우에도 작업 생성 전에 실패시킨다.

**Tech Stack:** Python 3.13, FastMCP v1, Pydantic v2, pytest

## Global Constraints

- 선택값과 학습자 정보는 MCP form Elicitation으로만 받는다.
- `init_work`의 작업 폴더 생성·기존 작업 교체보다 모든 필수 Elicitation과 검증이 먼저 끝나야 한다.
- 기존 작업의 `resume_work`, `init_work` resume, `scan_pdf` pending-setup 경로에는 새 필수 조건을 소급하지 않는다.
- 문제 내용 근거는 검토를 통과한 `summary`와 `key_points`로 제한하고, 학습자 정보는 난이도·용어·예시·관점 조정에만 사용한다.
- 구현 코드를 바꾸기 전에 실패하는 회귀 테스트를 먼저 확인한다.

---

### Task 1: 새 `init_work`의 학습자 정보 Elicitation을 필수화

**Files:**

- Modify: `tests/test_mcp_context.py`
- Modify: `tests/test_server.py`
- Modify: `tests/conftest.py`
- Modify: `server.py`

- [ ] **Step 1: 필수 form 계약과 공백 거부 회귀 테스트를 작성한다.**

  `tests/test_mcp_context.py`에 다음 동작을 고정한다.

  - 선택형 문제를 모두 끄더라도 네 번째 학습자 정보 form을 연다.
  - form JSON schema의 `required`에 `user_context`가 있고, `minLength`가 1이며, 필드명에 `(선택)`과 `default`가 없다.
  - `"   "` 제출은 실패하고 `result/<pdf-name>` 작업 폴더를 만들지 않는다.
  - 유효한 입력은 양끝 공백이 제거되어 상태에 저장된다.

  기존 `init_work` 성공 테스트와 fixture에는 의미 있는 학습자 정보를 넣고, `scan_pdf` 호환 테스트의 빈 입력은 유지한다.

- [ ] **Step 2: 회귀 테스트가 현재 구현에서 실패하는지 확인한다.**

  Run:

  ```bash
  rtk pytest -q tests/test_mcp_context.py tests/test_server.py
  ```

  Expected: 선택형 문제를 모두 끈 경우 form이 3개뿐이고, 학습자 정보 schema가 optional/default-empty라서 새 assertions가 실패한다.

- [ ] **Step 3: 공유 Elicitation 헬퍼에 `init_work` 전용 필수 모드를 구현한다.**

  `server.py`의 인터페이스를 다음과 같이 확장한다.

  ```python
  async def _elicit_question_setup(
      ctx: Context,
      setup: dict[str, Any],
      *,
      require_user_context: bool = False,
  ) -> dict[str, Any] | None:
  ```

  필수 모드에서는 다음 schema를 동적으로 만든다.

  ```python
  user_context=(
      str,
      Field(
          title="학습자 정보",
          description="학습 목적, 배경지식, 관심 분야, 현재 수준 등을 입력해주세요.",
          min_length=1,
      ),
  )
  ```

  `init_work`만 `require_user_context=True`로 호출한다. 응답을 `strip()`한 결과가 비어 있으면 작업 상태나 출력 폴더를 바꾸기 전에 명시적 오류를 반환한다. 기본값이 `False`인 기존 `scan_pdf` 호출은 현재의 선택 입력 및 skip 동작을 보존한다.

- [ ] **Step 4: 관련 테스트를 다시 실행한다.**

  Run:

  ```bash
  rtk pytest -q tests/test_mcp_context.py tests/test_server.py
  ```

  Expected: PASS.

- [ ] **Step 5: FastMCP 실제 Elicitation 왕복 계약을 갱신하고 확인한다.**

  실제 클라이언트 callback이 유효한 `user_context`를 반환하게 하고, 전송된 schema가 필수 계약인지 확인한다.

  Run:

  ```bash
  rtk pytest -q tests/test_mcp_context.py -k 'fastmcp or init_work'
  ```

  Expected: PASS.

- [ ] **Step 6: 구현과 테스트를 커밋한다.**

  ```bash
  rtk git add server.py tests/conftest.py tests/test_mcp_context.py tests/test_server.py
  rtk git commit -m "fix: require learner context for new work"
  ```

---

### Task 2: 학습자 정보가 문제 생성 프롬프트에 사용되는 계약 강화

**Files:**

- Modify: `tests/test_prompts.py`
- Inspect: `prompts.py`

- [ ] **Step 1: 현재 프롬프트 전달 경로를 확인한다.**

  `build_prompts`가 상태의 `user_context`를 포맷해 기본 문제와 확장 문제 prompt에 모두 포함하는지 확인한다. 기본 문제 지침은 난이도·용어 수준·예시의 친숙도·문제 관점 조정을, 확장 문제 지침은 난이도·현실 맥락 조정을 명시해야 한다.

- [ ] **Step 2: 전달값과 사용 지침을 함께 검증하는 characterization assertions를 추가한다.**

  `tests/test_prompts.py::test_user_context_and_book_info_are_injected`에서 실제 학습자 정보 문자열이 `basic_question_prompt`와 `extension_prompt`에 모두 존재하고, 각 prompt에 해당 조정 지침이 존재하는지 확인한다. 검토 prompt가 학습자 정보 때문에 원문 의미를 바꾸도록 유도하지 않는 현재 경계도 유지한다.

- [ ] **Step 3: 프롬프트 테스트를 실행한다.**

  Run:

  ```bash
  rtk pytest -q tests/test_prompts.py
  ```

  Expected: PASS. 기존 구현이 이미 계약을 충족하므로 production prompt 수정은 assertions가 실제 결손을 발견한 경우에만 한다.

- [ ] **Step 4: 계약 테스트를 커밋한다.**

  ```bash
  rtk git add tests/test_prompts.py prompts.py
  rtk git commit -m "test: enforce learner-aware question prompts"
  ```

---

### Task 3: 외부 계약·비즈니스 규칙·결정 기록 동기화

**Files:**

- Modify: `docs/contracts.md`
- Modify: `docs/business-rules.md`
- Modify: `docs/architecture.md`
- Modify: `docs/engineering-notes.md`
- Modify: `docs/adr/elicitation-order.md`
- Modify: `docs/pdf-learner-product-spec-ko.md`
- Modify: `docs/tracking/status.md`
- Modify: `docs/tracking/decisions/index.md`
- Add: `docs/tracking/decisions/0014-required-user-context.md`

- [ ] **Step 1: 현재 문서의 optional/blank/skip 표현을 모두 찾는다.**

  Run:

  ```bash
  rtk rg -n "학습자 정보|user_context|선택 입력|선택적" docs
  ```

  Expected: 새 작업에도 학습자 정보를 선택으로 설명하는 현재 문구가 출력된다.

- [ ] **Step 2: 현재 계약 문서를 새 동작에 맞춘다.**

  새 `init_work`는 세 문제 유형 form 이후 필수 학습자 정보 form을 항상 열고, 비어 있거나 공백뿐인 입력을 거부한다고 명시한다. 기존 작업 보완 흐름은 선택 입력을 유지하며, 문제 생성에서 학습자 정보가 내용 근거가 아니라 난이도·용어·예시·관점 조정 정보로 쓰인다는 경계를 유지한다.

- [ ] **Step 3: 결정 기록을 추가한다.**

  `0014-required-user-context.md`에 문제, 결정, 호환성, 결과를 기록하고 `0009`의 선택적 학습자 정보 결정 중 새 작업에 해당하는 부분을 대체한다고 명시한다. 결정 index를 갱신한다.

- [ ] **Step 4: 상충하는 현재형 문구가 남지 않았는지 확인한다.**

  Run:

  ```bash
  rtk rg -n "학습자 정보.*\(선택\)|user_context.*기본값|모두 제외.*건너" docs
  ```

  Expected: 역사적 결정의 명시적 superseded 설명 외에는 새 `init_work`와 충돌하는 현재형 문구가 없다.

- [ ] **Step 5: 문서를 커밋한다.**

  ```bash
  rtk git add docs
  rtk git commit -m "docs: require learner context for new work"
  ```

---

### Task 4: 전체 검증과 변경 범위 점검

**Files:**

- Verify: repository-wide tests and diff

- [ ] **Step 1: 전체 테스트를 실행한다.**

  Run:

  ```bash
  rtk pytest -q
  ```

  Expected: 모든 테스트 PASS.

- [ ] **Step 2: 변경 diff와 작업 트리를 검토한다.**

  Run:

  ```bash
  rtk git diff --check
  rtk git status --short
  rtk git log --oneline -5
  ```

  Expected: whitespace 오류가 없고, 변경은 필수 학습자 정보 계약·테스트·문서 범위에 한정된다.

- [ ] **Step 3: 완료 보고에 검증 증거와 호환성 범위를 포함한다.**

  새 `init_work`의 필수 입력, 기존 작업 호환성, 기본·확장 문제 prompt의 실제 활용 지침, 실행한 테스트 결과를 함께 요약한다.
