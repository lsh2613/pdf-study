# F-011 대기 작업 기반 재개 설계

## 목적

일부 챕터 결과가 이미 완료된 작업을 재개할 때 완료된 요약이나 확장 문제를 다시
생성하지 않는다. 요약과 확장 문제의 진행 상태가 서로 달라도 MCP 클라이언트가 남은
작업만 구조적으로 식별하고 정확한 저장 도구를 호출할 수 있어야 한다.

## 현재 문제

`resume_work`와 `list_pending_chapters`는 `summary_pending`과
`extension_pending`을 구분하지만, 다음 단계인 `get_subagent_prompts`는 모든
non-skip 챕터를 `chapter_ids`로 반환한다. 순차·병렬 workflow와 여러 도구의
`next_action`도 각 챕터에서 요약과 확장을 함께 만들도록 안내한다.

따라서 다음과 같은 비대칭 상태를 표현할 수 없다.

- `ch1`: 요약 완료, 확장 대기
- `ch2`: 요약 대기, 확장 완료

현재 안내를 따르면 `ch1`의 요약과 `ch2`의 확장을 다시 생성하고 기존 결과를
덮어쓸 수 있다. 또한 `get_subagent_prompts`가 완료된 챕터까지 raw 본문을 검증하므로,
더 이상 처리할 필요가 없는 챕터의 raw 파일 문제로 재개가 막힐 수 있다.

## 선택한 접근

대기 목록을 결과 종류별로 추가하고 기존 `chapter_ids`를 호환용 합집합으로 유지한다.

`get_subagent_prompts.data`는 다음 목록을 반환한다.

- `summary_pending_chapter_ids`: `summary_status`가 `completed`나 `skipped`가 아닌
  non-skip 챕터
- `extension_pending_chapter_ids`: 확장 문제가 활성화되어 있고
  `extension_status`가 `completed`나 `skipped`가 아닌 non-skip 챕터
- `chapter_ids`: 위 두 목록의 중복을 제거한 자연 정렬 합집합
- `skipped_chapter_ids`: 기존과 동일한 비본문 챕터 목록

`chapter_ids`는 기존 소비자가 처리 후보 챕터를 계속 찾을 수 있도록 유지한다. 어떤
결과를 생성할지는 두 개의 새 목록이 canonical 계약이다. 오래된 클라이언트가
`chapter_ids`만 읽고 workflow를 무시하는 경우까지 서버가 추측해 막지는 않는다.

## 구성과 데이터 흐름

### 대기 목록 계산

상태 객체 하나를 입력받아 요약·확장 대기 목록을 계산하는 순수 헬퍼를
`workspace.py`에 둔다. `resume_work`, `get_subagent_prompts`,
`list_pending_chapters`, `finalize_study`가 같은 완료 판정 규칙을 재사용한다.

호출 중 상태를 다시 읽어 서로 다른 시점의 목록을 섞지 않는다. 각 응답은 처음 읽은
하나의 state 스냅샷으로 모든 목록과 안내를 만든다. 목록 정렬은 `ch1`, `ch2`,
`ch10` 순서의 기존 자연 정렬 규칙을 따른다.

### 프롬프트와 workflow

순차와 병렬 workflow 모두 두 대기 목록을 명시적으로 사용한다.

- 요약 목록에만 있는 챕터는 `summarizer_prompt`와 `save_chapter_result`만 실행한다.
- 확장 목록에만 있는 챕터는 `extension_prompt`와 `save_extension_result`만 실행한다.
- 두 목록에 모두 있는 챕터는 `get_chapter_content`를 한 번 호출하고 같은 본문으로
  두 결과를 각각 생성·저장한다.
- 어느 목록에도 없는 챕터는 본문을 가져오거나 저장 도구를 호출하지 않는다.

병렬 모드의 최대 5개 제한은 목록 합집합의 챕터 작업 단위에 적용한다. 같은 챕터의
요약과 확장은 한 작업자가 같은 본문을 재사용하도록 안내한다.

### raw 본문 검증

`get_subagent_prompts`는 두 대기 목록의 합집합에 속한 챕터만 raw 본문 유효성을
검증한다. 대기 작업을 수행하려면 text와 정확한 `char_count`가 계속 필수다. 완료된
챕터는 현재 재개 작업의 입력이 아니므로 raw 파일이 없거나 손상되어도 프롬프트 반환을
막지 않는다.

### 다음 행동 안내

다음 응답의 `next_action`을 실제 대기 상태에 맞춘다.

- `resume_work`
- `get_subagent_prompts`
- `get_chapter_content`
- `save_chapter_result`
- `save_extension_result`
- `list_pending_chapters`

안내는 완료된 결과 종류를 다시 생성하라고 말하지 않는다. 모든 대기 목록이 비었으면
`finalize_study`로 진행하도록 안내한다. 응답 봉투 `{ok, error, data, next_action}`와
기존 오류 형태는 유지한다.

## 상태와 오류 처리

이 변경은 대기 상태를 읽고 안내하는 경계를 고치며 `state.json`의 상태 모델이나 저장
형식을 바꾸지 않는다. `pending`, `failed`, `in_progress`는 계속 대기로 간주하고
`completed`, `skipped`만 완료로 간주한다. 확장 문제가 비활성이면 확장 대기 목록은
항상 빈 배열이다.

결과 저장 도구가 완료된 파일의 명시적 재저장을 금지하는 쓰기 방어는 이번 범위에
포함하지 않는다. 기존 재생성 사용법을 깨는 별도 계약 변경이기 때문이다. 이번 수정은
서버가 제공하는 구조화 목록과 workflow가 완료 결과의 재생성을 지시하지 않도록 하는
데 집중한다.

## 외부 계약 호환성

새로운 두 대기 목록은 추가 필드다. 기존 `chapter_ids`, `skipped_chapter_ids`, 프롬프트,
모드 필드는 제거하지 않는다. 다만 `chapter_ids`의 의미는 “모든 non-skip 챕터”에서
“현재 처리할 작업이 하나 이상 남은 non-skip 챕터”로 좁아진다. 이는 완료 챕터를 다시
처리하게 하는 F-011을 해결하기 위한 의도된 동작 변경이다.

`resume_work`와 `list_pending_chapters`의 기존 `summary_pending`,
`extension_pending` 필드명은 유지한다. `get_subagent_prompts`의 새 필드는 프롬프트
처리 대상을 명확히 나타내기 위해 findings에 기록된 이름을 사용한다.

## 테스트

다음 회귀 테스트를 추가하거나 강화한다.

1. 모든 결과가 대기 중이면 두 새 목록과 `chapter_ids`가 모든 본문 챕터를 담는다.
2. 요약만 대기인 챕터는 요약 목록에만 나타나고 workflow가 확장을 다시 만들지 않는다.
3. 확장만 대기인 챕터는 확장 목록에만 나타나고 workflow가 요약을 다시 만들지 않는다.
4. 요약·확장 대기 목록이 서로 다른 상태에서 `chapter_ids`는 정확한 합집합이다.
5. 확장 비활성 작업의 확장 목록은 비어 있다.
6. skip 챕터는 모든 대기 목록과 `chapter_ids`에서 제외된다.
7. 모든 결과가 완료되면 세 처리 목록이 모두 비고 최종화 안내가 반환된다.
8. 완료 챕터의 raw 파일이 손상되어도 다른 대기 챕터의 프롬프트를 받을 수 있지만,
   대기 챕터의 raw 손상은 기존처럼 실패한다.
9. 순차·병렬 workflow와 관련 도구의 `next_action`이 실제 대기 종류만 안내한다.

변경 후 관련 단위·서버 테스트와 전체 테스트를 실행하고 문서 동등성, 포맷 오류,
작업 트리 범위를 확인한다.

## 문서 갱신과 완료 조건

구현과 함께 `AGENTS.md`, `CLAUDE.md`, `docs/architecture.md`,
`docs/business-rules.md`, `docs/contracts.md`, `docs/engineering-notes.md`,
`docs/standards.md`, `docs/tracking/status.md`, `docs/findings.md`에서 재개 계약과
검증 결과를 현재 동작에 맞춘다.

F-011은 다음 조건을 모두 만족한 뒤 완료로 기록한다.

- 완료된 요약·확장 결과를 workflow가 다시 처리하도록 안내하지 않는다.
- 비대칭 대기 상태가 구조화된 두 목록으로 표현된다.
- 대기 작업에 필요한 raw만 검증한다.
- 관련 테스트와 전체 테스트가 통과한다.
- 문서 변경과 구현을 커밋한다.
