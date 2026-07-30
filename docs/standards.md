# 작업 규칙

## 응답 계약

모든 MCP 도구는 `{ok, error, data, next_action}` 형태를 유지해야 한다. 새 도구나 기존 도구 변경이 이 봉투를 깨면 클라이언트가 다음 단계를 안정적으로 판단할 수 없다.

복구 가능한 입력 오류는 예외를 밖으로 던지지 않고 `ok=false`로 바꿔야 한다. 손상 PDF, 없는 파일, 잘못된 챕터 ID, 미지정 선택지는 사용자에게 고칠 수 있는 오류이므로 `error`와 `next_action`에 복구 방법이 있어야 한다.

사용자 선택은 선택을 소비하는 도구가 실행 직전에 여는 MCP form elicitation으로만
받아야 한다. 선택 파라미터와 `choices`, `user_choice_required`,
`user_choice_instruction` 같은 구조화 fallback을 공개 MCP 계약에 추가하면 안 된다.
선택 정의의 label과 설명은 private Elicitation 메시지 안에서 그대로 유지하고,
추천을 임의로 붙이거나 여러 선택을 합치면 안 된다.

Elicitation 거절·취소 또는 미지원 세션은 승인 뒤 처리 본문으로 넘어가거나 상태를
바꾸지 않아야 한다. 선택형 워크플로와 같은 작업을 수행하는 별도 동기 함수,
`_impl` 함수, MCP wrapper를 만들면 안 된다. `data.next_step.required_parameters`에는
에이전트가 생성할 값만 넣고 사용자 선택값은 넣지 않는다.

출력 경로는 `server.py`가 위치한 MCP 서버 프로젝트 루트 아래
`result/<pdf-name>`으로만 계산해야 한다. 공개 `output_dir`을 다시 추가하거나
요청 workspace, MCP root, `Path.cwd()`를 경로 기준으로 사용하면 안 된다.
결과 조회 도구는 같은 고정 result 루트의 직접 하위 디렉터리만 반환하고 상태를
변경하면 안 된다.

`get_subagent_prompts`는 요약과 확장 결과의 실제 pending 챕터 목록을 분리해 반환해야 한다. `completed`·`skipped`는 done, `pending`·`failed`·`in_progress`는 pending으로 판정하고, 확장 문제가 비활성이면 `extension_pending_chapter_ids`는 항상 빈 목록이어야 한다. 호환용 `chapter_ids`는 두 목록의 자연 정렬 합집합만 담는다. raw 검증은 원문이 필요한 summary pending에만 적용하고, extension-only pending은 완료된 저장 요약을 검증한다. workflow·`next_action`은 완료 챕터를 제외한 실제 pending 결과 유형과 그 유형에 필요한 입력 조회만 안내해야 한다.

## 상태 저장

`state.json`을 수정하는 코드는 `workspace.py`의 잠금 보호 헬퍼를 사용해야 한다. 병렬 챕터 처리 중 직접 파일을 읽고 다시 쓰면 한 챕터의 완료 상태가 다른 챕터 저장으로 덮일 수 있다.

`set_chapters`의 복구 가능한 입력 검증은 상태나 책 정보 파일을 쓰기 전에 끝내야 한다. 검증된 처리 모드, 챕터 목록과 setup/processing phase는 하나의 잠금 구간과 한 번의 `state.json` 저장으로 확정한다. 확정 뒤 본문 처리 실패는 setup을 롤백하지 않고 `chapter_processing=failed`와 챕터별 오류로 기록한다. 같은 작업의 setup과 본문 준비는 전체 호출 단위로 직렬화하고, 상태 저장 실패 뒤 책 정보 rollback도 실패하면 그 오류를 반드시 표면화한다.

JSON 저장은 임시 파일을 쓴 뒤 교체해야 한다. 중간에 프로세스가 죽어도 `state.json`, 요약 파일, 문제 파일이 반쯤 쓰인 상태로 남으면 안 된다.

`work_id`와 작업 폴더의 연결은 프로세스 메모리와 디스크 상태를 모두 고려해야 한다. 서버 재시작 후에는 `resume_work`로 디스크 상태를 다시 등록해야 하며, 알 수 없는 `work_id`를 새 작업처럼 처리하면 안 된다.

## PDF 처리

PDF 바깥으로 드러나는 페이지 번호는 1부터 시작해야 한다. 0부터 시작하는 페이지 번호는 PDF 라이브러리 호출 경계 안에서만 사용하고, 상태 파일·도구 응답·테스트에는 노출하면 안 된다.

챕터의 PDF 추출 범위는 `pdf_pages`, 원문에 표시된 선택적 페이지 번호는 `source_pages`를 사용해야 한다. `source_pages`는 표시 메타일 뿐 추출 경계로 사용하면 안 되며, 새 저장·응답에 구형 `page_range`·`printed_range`를 다시 만들면 안 된다.

챕터 경계 판단에 텍스트 추출 목차 파서를 추가하면 안 된다. 내장 목차 또는 목차 페이지 이미지가 아닌 경로로 챕터를 자동 구성하면 스캔본과 깨진 PDF에서 잘못된 페이지 범위가 저장된다.

OCR 모드에서 서버는 `set_chapters` 반환 전에 non-skip 챕터의 페이지 이미지를 PaddleOCR CPU로 읽고 raw 본문을 저장해야 한다. 하나의 챕터 안에서는 페이지 순서를 지키고, 여러 챕터만 서버 프로세스 전역 worker 수 제한 안에서 병렬 처리한다. 페이지 OCR 예외나 챕터 전체 공백 결과가 있으면 partial raw 본문을 저장하지 말고 실패 상태와 오류를 남겨야 한다.

## 렌더링

렌더러는 `chapters/{summaries,quiz,extension_quiz}`와 `raw_data`에 저장된 결과만 읽어야 한다. 최종 렌더링 중 PDF를 다시 읽거나 요약을 새로 생성하면 같은 작업의 재현성이 깨진다.

HTML과 Markdown+TUI는 같은 중립 JSON에서 만들어져야 한다. 한 포맷만 읽을 수 있는 별도 스키마를 추가하면 두 출력 방식의 내용이 갈라진다.

렌더러는 비어 있는 staging 폴더에 한 세대를 완성하고, 최종 폴더 교체는 `.pdf-learner-manifest.json`에 기록된 관리 경로에만 적용해야 한다. manifest 밖의 사용자 파일을 삭제하거나 덮어쓰면 안 되며, 실패하면 이전 세대와 manifest를 복원해야 한다.

진도는 출력 형식과 학습 fingerprint가 모두 같을 때만 재사용한다. 챕터 구성이나 생성 내용이 달라졌는데 파일 이름만 같다는 이유로 이전 progress를 유지하면 안 된다.

마크다운 요약은 HTML에서 원시 텍스트로 노출되면 안 된다. `markdown-it-py`가 없더라도 내장 변환기가 제목, 목록, 표, 코드 같은 기본 문법을 읽을 수 있게 해야 한다.

## 의존성과 실행 환경

MCP 실행은 저장소 안의 `.venv`를 기준으로 안내해야 한다. 전역 Python이나 사용자의 다른 프로젝트 가상환경을 기본값으로 문서화하면 클라이언트 설정이 재현되지 않는다.

런타임 의존성을 직접 import하는 코드가 생기면 `pyproject.toml`에 명시해야 한다. 전이 의존성에 기대어 직접 import하면 설치 환경 검증이 거짓 양성이 된다.

검증은 변경 범위에 맞는 pytest를 실행해야 한다. PDF 처리, 서버 계약, 렌더러, 설치 스크립트 중 어느 하나를 바꾸고 관련 테스트 없이 완료로 보고하면 안 된다.
