# 요청 workspace 경로와 서버 주도 사용자 선택

## 상태

채택

## 결정

- 빈 `output_dir`과 상대 경로는 MCP 요청에서 확인한 단일 Codex workspace를
  기준으로 해석한다. Codex 메타가 없으면 단일 MCP root를 사용한다.
- 현재 agent workspace를 하나로 식별할 수 없으면 절대 `output_dir`을 요구하고,
  MCP 서버 프로세스의 `Path.cwd()`로 폴백하지 않는다.
- MCP form elicitation을 지원하는 클라이언트에서는 문제 유형, OCR 언어, 챕터
  구성·범위와 처리 모드, 출력 형식, 기존 작업 재개·교체, `.work` 정리를 실행 직전에
  서버가 직접 요청한다.
- `set_chapters`는 챕터 구성·범위 확인, text/OCR 본문 추출 방식,
  sequential/parallel 실행 방식을 이 순서의 독립된 세 form으로 요청하고, 모두
  승인된 뒤에만 기존 sync 구현을 호출한다.
- elicitation 응답은 에이전트가 도구 호출에 먼저 채운 선택 인자보다 우선한다.
  거절·취소 시 기존 sync 구현을 호출하지 않아 상태와 파일을 바꾸지 않는다.
- 새 작업 생성 elicitation은 해석된 절대 `output_dir`을 함께 보여주고 확인받는다.
- elicitation 미지원 클라이언트에는 기존 `user_choice_required`,
  `user_choice_instruction`, 구조화된 `choices`와 입력 검증 계약을 유지한다.

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
- 모든 클라이언트에서 elicitation을 필수화: 미지원 MCP 클라이언트의 기존 흐름을
  즉시 깨므로 구조화 선택 fallback을 유지한다.
