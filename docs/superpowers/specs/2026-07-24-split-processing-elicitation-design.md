# 처리 방식 Elicitation 분리 설계

## 배경

현재 `set_chapters`의 MCP 래퍼는 챕터 구성·범위 확인과 처리 방식을 하나의 form
elicitation으로 요청한다. 처리 방식도 `Sequential + Text`, `Parallel + Text`,
`Sequential + OCR`, `Parallel + OCR`의 조합 네 개 중 하나를 고르게 되어 있어,
사용자는 서로 독립적인 본문 추출 방식과 실행 방식을 한 번에 판단해야 한다.

## 목표

- `set_chapters` 한 번의 MCP 도구 호출 안에서 사용자 선택을 세 번의 독립된
  elicitation으로 요청한다.
- 요청 순서는 챕터 구성·범위 확인, 본문 추출 방식, 실행 방식으로 고정한다.
- 세 요청이 모두 승인되기 전에는 기존 동기식 `set_chapters`를 호출하거나 작업 상태를
  변경하지 않는다.
- 에이전트가 도구 인자에 미리 넣은 `extraction_mode`와 `execution_mode`보다 각
  elicitation 응답을 우선한다.
- elicitation 미지원 클라이언트의 기존 구조화된 네 조합 선택 계약은 호환을 위해
  유지한다.

## 비목표

- `set_chapters`를 여러 MCP 도구로 분리하지 않는다.
- 중간 선택을 `.work/state.json`에 저장하지 않는다.
- `set_chapters`의 공개 입력 파라미터나 동기식 검증·본문 준비 로직을 바꾸지 않는다.
- 텍스트 품질이 나쁜 PDF에서 text 모드를 다시 허용하지 않는다.

## 설계

### 1. 챕터 구성·범위 확인

첫 번째 elicitation은 `recommendations.user_choice_options`의 챕터 구성 전략과 호출에
들어온 챕터 제목·`pdf_pages`를 표시한다. 응답은 선택지가 있을 때의
`chapter_strategy`와 항상 필요한 `chapters_confirmed`만 담는다.

- 사용자가 거절·취소하거나 `chapters_confirmed=false`를 제출하면 즉시
  `_elicitation_cancelled`를 반환한다.
- `chapter_strategy=reanalyze_with_vision`이면 챕터 설정을 실행하지 않고 기존
  `force_vision=True` 복구 안내를 반환한다.
- 이 단계에서는 추출 방식과 실행 방식을 묻지 않는다.

### 2. 본문 추출 방식 선택

두 번째 elicitation은 `extraction_mode` 하나만 요청한다.

- 정상 텍스트 레이어: `text`, `ocr` 두 선택지를 표시한다.
- `garbled` 또는 `no_text_layer`: `ocr`만 표시하고 `text`는 스키마에서도 허용하지
  않는다.
- 선택지는 `processing_mode_contract`의 분리된 canonical 정의에서 가져온다.
- 거절·취소 시 세 번째 요청과 실제 `set_chapters` 호출을 실행하지 않는다.

### 3. 실행 방식 선택

세 번째 elicitation은 `execution_mode` 하나만 요청하며 `sequential`, `parallel` 두
선택지를 표시한다. OCR 본문 선처리의 서버 내부 동시성 상한과 sub-agent 실행 방식이
서로 다른 개념이라는 설명을 유지한다.

거절·취소 시 실제 `set_chapters`를 호출하지 않는다. 승인되면 두 번째와 세 번째
응답에서 얻은 값을 사용해 기존 동기식 `set_chapters`를 정확히 한 번 호출한다.

## 컴포넌트 변경

`processing_mode_contract.py`는 기존 조합형 `choices()`를 호환용으로 유지하면서,
elicitation 전용 `extraction_choices(text_quality)`와 `execution_choices()`를
제공한다. 분리 선택지는 조합형 정의와 같은 의미를 공유해 설명이 서로 어긋나지 않게
한다.

`server.py`의 `_elicit_processing_setup`은 다음 세 책임으로 분리한다.

- `_elicit_chapter_setup`
- `_elicit_extraction_mode`
- `_elicit_execution_mode`

`_mcp_set_chapters_tool`은 이 세 함수를 순서대로 호출하고 각 단계의 중단 결과를 처리한
후 동기식 `set_chapters`에 최종 두 모드를 전달한다.

## 오류와 상태 보존

각 elicitation은 `action="accept"`이고 스키마 검증을 통과한 `data`가 있을 때만
승인으로 본다. `decline`, `cancel`, 빈 데이터, 확인 boolean의 `false`는 상태 변경 없는
오류 응답으로 끝낸다.

앞선 elicitation을 승인한 뒤 다음 elicitation을 취소해도 승인값은 메모리에만 있었으므로
rollback할 상태가 없다. 실제 작업 상태 변경은 세 단계가 모두 끝난 뒤 동기식
`set_chapters`가 담당한다.

## 호환성

MCP form elicitation을 지원하지 않는 클라이언트는 계속 `data.next_step.choices` 또는
오류 fallback의 네 조합 선택지를 사용한다. `set_chapters`의 파라미터와 응답 봉투도
변경하지 않는다.

elicitation 지원 클라이언트에서만 UI 요청 횟수가 한 번에서 세 번으로 바뀐다.

## 테스트

- 챕터, 추출 방식, 실행 방식 메시지가 정확히 세 번 발생하고 각 메시지에 다른 책임의
  선택지만 포함되는지 검증한다.
- 에이전트가 `sequential/text`를 넣고 elicitation이 `parallel/ocr`을 선택했을 때
  elicitation 값이 저장되는지 검증한다.
- 첫 번째, 두 번째, 세 번째 요청 각각의 거절·취소가 동기식 `set_chapters` 호출과 상태
  변경을 막는지 검증한다.
- 텍스트가 깨지거나 없는 PDF의 추출 방식 요청에는 `ocr`만 허용되고 `text`가 노출되지
  않는지 검증한다.
- 실제 FastMCP client/server 왕복 테스트가 세 번의 elicitation 응답을 순서대로
  처리하는지 검증한다.
- elicitation 미지원 경로의 기존 네 조합 응답 테스트를 유지한다.
