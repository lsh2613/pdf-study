# 요청 workspace 경로와 서버 주도 사용자 선택

## 상태

부분 대체 — 경로 결정은 0011, 새 작업의 선택적 학습자 정보는 0014가 대체한다.

## 결정

- 공개 MCP에서 `output_dir`을 제거한다. MCP 요청에서 확인한 단일 Codex
  workspace를 기준으로 `result/<pdf-name>`을 계산하고, Codex 메타가 없으면 단일
  MCP root를 사용한다.
- 현재 agent workspace를 하나로 식별할 수 없으면 상태 변경 없이 실패하고 MCP 서버
  프로세스의 `Path.cwd()`로 폴백하지 않는다.
- MCP form elicitation으로 문제 유형, 선택적 학습자 정보, OCR 언어, 챕터
  구성·범위와 처리 모드, 출력 형식, 기존 작업 재개·교체, `.work` 정리를 실행 직전에
  서버가 직접 요청한다.
- `set_chapters`는 챕터 구성 방식, text/OCR 본문 추출 방식,
  sequential/parallel 실행 방식을 이 순서의 독립된 form으로 요청한다. 직접 입력과
  균등 청크는 범위 또는 청크 크기 form을 조건부로 추가하고, 필요한 form을 모두
  승인한 뒤에만 처리 본문을 실행한다.
- 공개 MCP 입력에서 사용자 선택 파라미터를 제거한다. elicitation 응답만 기존
  sync 구현으로 전달하며 거절·취소 시 sync 구현을 호출하지 않는다.
- 새 작업 생성 elicitation은 계산된 절대 출력 경로를 안내한다. 단답형·주관식·확장형
  중 하나라도 포함할 때만 선택적 `user_context` form을 이어서 열며, 생략하거나 빈
  값으로 승인할 수 있다.
- Codex가 다중 필드 form의 탐색 순서를 재배열할 수 있으므로 단답형·주관식·확장형은
  각각 단일 필드 form으로 이 순서대로 요청한다. 필요한 선택과 조건부 context를
  모두 승인하기 전에는 작업 또는 스캔 상태를 변경하지 않는다.
- 공개 응답에서 `choices`, `user_choice_required`, `user_choice_instruction`,
  `question_setup`, `ocr_language_setup` 같은 구조화 fallback을 제거한다.
- Elicitation 미지원 클라이언트는 `required_capability="elicitation.form"`으로
  fail-closed한다.
- Form 필드 제목은 한국어로 명시하고, 선택지는 한글 이름으로 표시한다. 구분에
  필요한 설명만 같은 문자열 enum 항목에 덧붙이며 자명한 설명은 생략한다. `message`는
  출력 경로, 챕터 목록, 제한과 위험처럼 단계 고유 정보만 담고 선택지를 반복하지
  않는다. 표시용 값은 승인 뒤 기존 내부 상태값으로 변환한다.

## 이유

장시간 실행되는 stdio MCP 서버의 cwd는 호출별 agent cwd와 같다는 보장이 없다.
`Path.cwd()` 기본값과 에이전트가 간헐적으로 넣는 절대 경로가 섞이면 같은 PDF의
결과가 서로 다른 폴더를 오간다.

또한 구조화된 선택지와 강한 지시문만으로는 일반 도구 인자가 실제 사용자 답에서
왔는지 서버가 확인할 수 없다. MCP elicitation은 선택을 실행 경계에서 다시 받아
그 응답을 직접 사용할 수 있는 표준 채널이다.

## 대안

- 서버 cwd를 계속 기본값으로 사용: 호출별 작업공간을 반영하지 못한다.
- agent가 항상 절대 `output_dir`을 넣도록 문구만 강화: 현재와 같은 비결정적
  누락을 서버가 막을 수 없다.
- `user_confirmed=true` 같은 boolean이나 opaque token을 추가: 에이전트가 그대로
  채울 수 있어 사람의 선택을 증명하지 못한다.
- 미지원 클라이언트에 구조화 fallback 유지: 에이전트가 Elicitation을 피하고
  일반 도구 인자로 임의 선택할 경로가 남으므로 채택하지 않는다.
