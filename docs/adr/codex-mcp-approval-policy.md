# Codex MCP 승인 정책 복구 결정

## 상태

채택 — 2026-08-03

## 배경

`scripts/setup_mcp.sh`는 프로젝트 로컬 `.venv`를 준비한 뒤 Codex CLI에
pdf-learner MCP 서버를 등록한다. 과거 설치 흐름은 `approval_policy`의 inline table을
여러 줄로 작성했다. TOML inline table은 한 줄이어야 하므로, 이 설정은 Codex가
`config.toml`을 읽는 단계에서 문법 오류를 일으켰다.

문법을 고쳐 `mcp_elicitations = true`만 둔 `[approval_policy.granular]` table을
생성했지만, 현재 Codex CLI는 granular 정책을 사용할 때 `sandbox_approval`, `rules`,
`request_permissions`, `skill_approval`, `mcp_elicitations` 다섯 필드를 모두 요구한다.
필드가 빠지면 MCP 등록 전에 설정 해석이 실패한다.

pdf-learner의 `init_work` 등 선택형 도구는 MCP form elicitation으로만 사용자 선택을
받는다. Elicitation을 표시할 수 없는 실행 환경에서는 상태를 바꾸지 않고 실패해야
한다.

## 결정

- 설치 스크립트는 Codex CLI만 전역 MCP 설정 대상으로 한다. Claude Code와
  Antigravity CLI 설정 처리는 제거한다.
- Codex의 `approval_policy`가 없거나 정확히 `"never"`이면 다음 table 형식의 granular
  정책을 기록한다.

  ```toml
  [approval_policy.granular]
  sandbox_approval = true
  rules = true
  mcp_elicitations = true
  request_permissions = true
  skill_approval = true
  ```

- 기존 granular 정책에 다섯 필드 중 누락 또는 `false` 값이 있으면 다섯 값을 모두
  `true`로 바꾼다. 이는 자동 승인이 아니라 해당 범주의 승인 요청을 사용자에게
  표시한다는 뜻이다.
- 과거 setup flow가 만든 것으로 식별 가능한 여러 줄 `approval_policy = { granular = {
  ... } }` 형식만 위 table 형식으로 복구한다. 변경 전 `config.toml`은
  `config.toml.pdf-learner.bak`으로 백업한다.
- 그 밖의 TOML 문법 오류, 예상하지 못한 policy 형태, 명시적인 명령줄 옵션, profile,
  managed policy는 자동으로 수정하거나 우회하지 않는다.
- `codex mcp add`로 서버를 등록하고 `codex mcp get pdf-learner`로 등록을 확인한다.
  서버 table에는 `default_tools_approval_mode = "approve"`를 설정해 pdf-learner의
  일반 MCP 도구 호출을 자동 승인한다. 이 설정은 MCP elicitation 승인 정책과 별개다.

## 검증 규칙

- `tests/test_mcp_config.py`는 누락·`false` granular 필드를 포함한 유효 TOML이 다섯
  `true` 값으로 바뀌는지 검사한다.
- 과거의 알려진 여러 줄 inline-table 오류는 백업을 만들고 유효 TOML로 복구되는지
  검사한다.
- 알려지지 않은 TOML 오류는 원본 파일과 백업을 변경하지 않고 실패하는지 검사한다.
- Codex 등록은 `codex mcp add`와 `codex mcp get pdf-learner` 호출 순서로 검사한다.
- 설치 스크립트는 Codex만 설정하는지, shell 문법이 유효한지 검사한다.

## 결과와 trade-off

- 처음 설치하는 사용자와 과거의 잘못된 policy를 가진 사용자 모두 현재 Codex가 읽을
  수 있는 설정을 얻는다.
- granular 정책의 모든 범주가 interactive이므로, 이전의 `never` 정책보다 사용자에게
  더 많은 승인 요청이 나타날 수 있다. 누락 필드 때문에 Codex 자체가 시작하지 못하는
  위험을 피하기 위해 이 비용을 수용한다.
- `default_tools_approval_mode = "approve"`는 pdf-learner 도구 호출 승인만 생략하며,
  form에서 받는 사용자 선택을 자동으로 대답하지 않는다.
- `--ask-for-approval never`, `--yolo`,
  `--dangerously-bypass-approvals-and-sandbox`는 전역 설정보다 우선하며 승인 form을
  표시하지 않는다. 이 MCP의 선택형 흐름에는 사용하지 않는다. 전체 파일 접근이
  필요하면서 form을 유지하려면 `codex --sandbox danger-full-access`를 사용한다.
