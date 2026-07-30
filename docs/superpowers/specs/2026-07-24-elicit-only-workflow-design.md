# Elicitation 전용 사용자 선택 설계

## 배경

현재 MCP 공개 도구는 form elicitation을 지원하면서도 같은 사용자 선택값을 일반 도구
파라미터로 받을 수 있다. Elicitation 미지원 세션에서는 구조화된 선택지와 호출 인자를
사용하는 fallback도 남아 있다. 이 중복 경로 때문에 agent가 사용자 확인 없이 선택
파라미터를 직접 채워 다음 단계를 실행할 수 있다.

또한 `output_dir`을 호출 인자로 받을 수 있어 agent가 현재 workspace가 아닌 경로를
전달할 여지가 있다. 출력 위치는 MCP 서버 프로세스 cwd나 agent 제공값이 아니라 현재
Codex workspace를 기준으로 결정해야 한다.

## 목표

- 사용자 결정값은 MCP form elicitation 응답으로만 받는다.
- 사용자 선택 파라미터를 MCP 공개 도구 스키마에서 제거한다.
- Elicitation 미지원 클라이언트의 선택 fallback을 제거하고 fail-closed로 처리한다.
- 출력 폴더는 요청의 단일 Codex workspace 또는 단일 MCP file root를 기준으로
  `<workspace>/result/<pdf-name>`으로 서버가 직접 계산한다.
- `user_context`는 Elicitation에서 받되 사용자가 생략할 수 있다.
- 사용자 선택이 아닌 작업 식별자와 처리 payload는 기존 MCP 도구 파라미터로 유지한다.

## 출력 경로

`init_work`의 MCP 공개 입력은 `pdf_path`만 받는다. 서버는 요청 메타의
`x-codex-turn-metadata.workspaces`에서 유효한 절대 디렉터리를 읽는다. 단일 Codex
workspace가 없으면 단일 MCP file root를 사용한다.

선택된 workspace 아래 다음 경로를 계산한다.

```text
<workspace>/result/<sanitized-pdf-stem>
```

`output_dir`은 도구 파라미터나 Elicitation 입력 필드로 받지 않는다. 첫 작업 설정
Elicitation의 메시지에 계산된 절대 경로를 읽기 전용으로 표시한다.

workspace가 없거나 여러 개여서 모호하면 서버 cwd로 폴백하지 않고 상태 변경 없이
실패한다. 이 경로는 MCP 표준의 임의 shell cwd가 아니라 Codex가 요청에 제공한 현재
workspace root다.

## Elicitation capability

사용자 선택이 필요한 MCP 도구는 실행 전에 클라이언트의 form elicitation capability를
검사한다. 지원하지 않으면 일반 호출 인자를 사용하는 대신 `ok=false`와
`required_capability="elicitation.form"`을 반환한다.

선택이 없는 본문 조회, 결과 저장, 상태 조회 같은 도구는 Elicitation이 필요하지 않으며
계속 제공한다. 제거 대상은 이 필수 도구들이 아니라 사용자 선택을 대신 받던 파라미터와
구조화 fallback 경로다.

## MCP 공개 도구 계약

### `init_work(pdf_path)`

서버가 출력 경로를 계산하고 기존 상태를 검사한다.

새 작업이면 하나의 form에서 다음 값을 받는다.

- `enable_short_answer: bool`
- `enable_reflection: bool`
- `enable_extension: bool`
- `user_context: str | None`

객관식은 기존 호환 규칙대로 내부에서 활성화하며 공개 선택 파라미터를 두지 않는다.
`user_context`는 optional이고, 누락 또는 빈 문자열이면 학습자 정보 없이 진행한다.
폼 메시지는 계산된 출력 경로를 표시하지만 출력 경로 확인 boolean이나 수정 필드는
포함하지 않는다.

고정 출력 경로에 기존 관리 작업이 있으면 별도의 form에서 `resume` 또는 `replace`를
선택한다. `resume`은 기존 작업을 등록하고, `replace`는 기존 관리 `.work`를 안전 규칙에
따라 교체한 뒤 새 작업 설정 form을 연다. 재개할 상태가 없으면 `replace`만 허용한다.

pdf-learner가 관리하지 않는 파일과 충돌하면 출력 경로를 임의 변경하거나 파일을
덮어쓰지 않고 실패한다. `new_output_dir` 선택은 제거한다.

제거하는 공개 파라미터:

- `output_dir`
- `enable_multiple_choice`
- `enable_short_answer`
- `enable_reflection`
- `enable_extension`
- `user_context`
- `replace_existing`

### `resume_work(pdf_path)`

`pdf_path`와 현재 workspace로 고정 출력 경로를 계산한다. `output_dir` 공개 파라미터는
제거한다. 기존 작업을 실제 등록하기 전에 `resume_confirmed` Elicitation을 실행한다.
미정 문제 유형이 있는 구형 작업은 이후 `scan_pdf`가 Elicitation으로 확정한다.

### `scan_pdf(work_id, scan_size=30, force_vision=False)`

문제 유형과 학습자 정보 파라미터를 제거한다. 작업 상태에 미정 문제 유형이 있으면
Elicitation으로 필수 boolean과 optional `user_context`를 받은 뒤 스캔한다. 이미 확정된
작업은 저장된 값을 사용하므로 새 사용자 선택 요청이 필요 없다.

제거하는 공개 파라미터:

- `enable_short_answer`
- `enable_reflection`
- `enable_extension`
- `user_context`

### `prepare_ocr(work_id)`

`ocr_language` 공개 파라미터를 제거한다. 한국어·영어 선택은 Elicitation 응답만 사용한다.

### `set_chapters(work_id, chapters, book_info=None)`

챕터 구성·범위 확인, text/OCR 추출 방식, sequential/parallel 실행 방식의 세 form을
순서대로 실행한다. `chapters`와 `book_info`는 agent가 분석한 처리 payload이므로
파라미터로 유지하지만, 사용자가 첫 form에서 챕터 제목과 PDF 범위를 승인해야 한다.

제거하는 공개 파라미터:

- `execution_mode`
- `extraction_mode`
- `ocr_language`

OCR을 선택했지만 언어와 모델이 준비되지 않았으면 상태를 변경하지 않고
`prepare_ocr(work_id)`를 호출하도록 안내한다.

### `finalize_study(work_id)`

`output_format` 공개 파라미터를 제거하고 HTML 또는 Markdown/TUI를 Elicitation으로만
선택한다. `keep_work_dir`도 제거하고 항상 중간 작업을 보존한다. 중간 데이터 삭제는
별도 `cleanup_work` 확인 form에서만 수행한다.

### `cleanup_work(work_id)`

공개 선택 파라미터 없이 기존 확인 form을 유지한다. Elicitation 미지원, 거절, 취소,
`cleanup_confirmed=false`이면 삭제하지 않는다.

## 구조화 fallback 제거

MCP 응답에서 agent가 다음 호출의 사용자 선택값을 직접 구성하게 만드는 항목을 제거한다.

- `user_choice_required`
- `user_choice_instruction`
- 사용자 선택용 `next_step.choices`
- 오류 복구용 사용자 선택 `data.choices`
- 순차/병렬과 text/OCR의 네 조합 공개 fallback
- `new_output_dir`

`next_step`은 다음 도구명과 agent가 준비해야 하는 기계적 입력만 안내한다. 예를 들어
`set_chapters` 단계의 필수 입력은 `chapters`이며 처리 방식은 도구 내부 Elicitation이
받는다. `prepare_ocr`와 `finalize_study`는 선택 파라미터 없이 도구명만 안내한다.

서버 내부의 선택지 label과 설명은 Elicitation 메시지와 JSON Schema enum 생성에
계속 사용한다.

## 내부 sync 함수

기존 sync 함수는 검증, 상태 저장, 렌더링 구현을 재사용하기 위해 유지한다. 이 함수들의
선택 파라미터는 서버 내부에서 Elicitation 응답을 전달하는 용도이며 MCP 도구로 직접
등록하지 않는다.

따라서 외부에는 Elicitation 전용 비동기 래퍼만 보이고, 내부에는 하나의 비즈니스
로직만 남는다.

## 오류와 상태 보존

- capability 부족은 Elicitation 요청이나 상태 변경 전에 실패한다.
- 거절·취소·빈 응답·허용 목록 밖의 값은 sync 함수를 호출하지 않는다.
- 세 단계 `set_chapters` 선택은 모두 메모리에만 두고 전부 승인된 뒤 한 번 저장한다.
- 기존 출력 충돌은 관리 manifest와 `.work` 규칙을 그대로 적용한다.
- unmanaged 파일 충돌은 자동 삭제·덮어쓰기·대체 경로 생성을 하지 않는다.

## 테스트

- 공개 MCP tool schema에 제거 대상 선택 파라미터가 존재하지 않는지 검증한다.
- Elicitation 미지원 세션에서 선택 도구가 fail-closed이고 상태·파일을 바꾸지 않는지
  검증한다.
- Codex request metadata의 단일 workspace에서 출력 경로가 정확히 계산되는지 검증한다.
- `init_work` form에서 출력 경로는 메시지에만 표시되고 `user_context`는 생략 가능한지
  검증한다.
- 기존 출력에서 resume/replace가 Elicitation 응답으로만 실행되는지 검증한다.
- OCR 언어, 챕터 설정, 최종 형식, cleanup이 호출 인자 없이 실제 FastMCP 왕복에서
  동작하는지 검증한다.
- next-step과 오류 응답에 제거된 choice fallback이 남지 않는지 검증한다.
- 기존 본문 추출·저장·렌더링 sync 로직의 회귀 테스트를 유지한다.
