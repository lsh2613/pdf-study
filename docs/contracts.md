# 외부 계약

## 공통 응답

모든 MCP 도구는 다음 형태를 반환한다.

```json
{
  "ok": true,
  "error": null,
  "data": {},
  "next_action": "다음에 호출할 작업"
}
```

성공이면 `ok=true`, 실패면 `ok=false`다. 실패 응답의 `data`는 진단값이나 누락된
에이전트 입력을 담을 수 있지만 사용자 선택 fallback은 담지 않는다. 클라이언트는
예외 대신 이 봉투를 보고 다음 단계를 정해야 한다.

사용자 선택값은 선택을 소비하는 도구가 실행 중에 여는 MCP form elicitation
응답으로만 받는다. 공개 입력 스키마에는 선택 파라미터가 없고, 공개 성공·실패
응답에도 `choices`, `user_choice_required`, `user_choice_instruction` 같은 구조화
fallback이 없다. Elicitation을 지원하지 않는 세션은
`data.required_capability="elicitation.form"`으로 상태 변경 없이 실패한다.
거절·취소도 선택 뒤의 실행 본문으로 넘어가지 않는다.

Form의 `requestedSchema`는 Codex가 처리할 수 있는 MCP primitive schema
부분집합만 사용한다. 최상위 키는 `$schema`, `type`, `properties`, `required`로
제한하고 Pydantic 모델 이름인 `title`을 전송하지 않는다. 선택적 문자열은
`string | null`의 `anyOf`로 표현하지 않고 `type="string"`, `default=""`로
표현한다. 새 `init_work`의 학습자 정보는 선택적 문자열이 아니라
`required=["user_context"]`, `minLength=1`, default 없음인 필수 문자열이다.

각 form 필드는 한국어 표시 문구를 명시해 클라이언트가 내부 snake_case 필드명을
영문 제목으로 자동 변환하지 않게 한다. Codex가 다중 필드 form의 탐색 순서를
재배열할 수 있으므로 문제 유형은 각각 단일 필드 form으로 열고, 서버 호출 순서로
`단답형 → 주관식 → 확장형`을 보장한다. 각 form의 `message`에는 먼저 읽어야 할
질문·설명을, 필드 `title`에는 짧은 선택 항목명(`단답형 문제 생성` 등)을 둔다.
문자열 선택지는
간결한 한글 이름으로 표시하고, 구분에 필요한 설명이 있을 때만 `이름 — 설명` 형태로
enum 값에 붙인다. 자명한 설명은 생략한다. `html`, `md+tui`, OCR처럼
고유 형식명이나 약어는 그대로 쓸 수 있다. 표시용 enum 값은 Elicitation을 처리하는
같은 async 함수 안에서 기존 내부값(`parallel`, `md_tui` 등)으로 변환하므로 상태와
렌더러 계약은 바뀌지 않는다.

`message`에는 챕터 목록, 텍스트 품질에 따른 제한, 삭제 후 재개 불가처럼
해당 단계에서 별도로 알아야 하는 정보만 넣는다. 필드 선택지와 같은 label·설명을
`message`에 다시 나열하지 않는다. 선택지의 정보는 `requestedSchema`의 enum에만
한 번 표시한다.

선택을 소비하는 각 워크플로는 form Elicitation과 승인 후 실행을 하나의 등록된
async 함수에서 처리한다. `pdf_learner.server`에는 같은 작업을 선택 인자로 바로
실행하는 별도 `_impl` 함수나 별도 MCP wrapper가 없다. 클라이언트는 모듈의 하위
primitive를 조합하지 않고 MCP 세션으로 아래 도구를 호출해야 한다.

선택을 포함하는 공개 MCP 스키마는 다음과 같다.

```text
init_work(pdf_path)
resume_work(pdf_path)
scan_pdf(work_id, scan_size=30, force_vision=false)
prepare_ocr(work_id)
set_chapters(work_id, chapters, book_info=null)
finalize_study(work_id)
cleanup_work(work_id)
```

선택 없는 결과 경로 조회 스키마는 `list_study_results()`다.

`data.next_step.required_parameters`에는 에이전트가 생성하거나 전달해야 하는 값만
들어간다. `set_chapters`는 `chapters`, `prepare_ocr`와 `finalize_study`는 빈 배열을
사용한다.

## 도구 흐름

`init_work(pdf_path)`는 `server.py`가 위치한 MCP 서버 프로젝트 루트 아래
`result/<pdf_basename>`을 고정 출력 폴더로 계산한다. 공개 `output_dir`은 없고,
요청 workspace, MCP file root, 프로세스 cwd에 따라 경로가 달라지지 않는다. 기존
관리 작업은 `resume`/`replace` Elicitation으로 처리하고, 관리되지 않은 파일이
있으면 덮어쓰지 않는다. 새 작업은 단답형·주관식·확장형의 단일 필드 form을 이
순서로 승인한 뒤, 세 선택과 무관하게 필수 `user_context` form을 네 번째로 연다.
학습자 정보는 비어 있지 않아야 하며 공백뿐인 값도 서버 검증에서 거부한다. 필요한
form을 모두 승인하고 학습자 정보를 검증한 뒤에만 작업을 만든다. 기존 작업의
미확정 설정을 보완하는 `scan_pdf` 흐름은 호환성을 위해 선택적 학습자 정보와 빈
문자열을 계속 허용한다.

`resume_work(pdf_path)`는 같은 고정 경로의 `.work/state.json`을
`resume_confirmed` Elicitation 승인 뒤 다시 등록한다.

`list_study_results()`는 같은 서버 프로젝트 루트의 `result/*`에서 숨김 항목,
파일, 심볼릭 링크를 제외한 직접 하위 디렉터리를 정렬해 조회한다. 성공 응답의
`data.result_root`는 고정 result 루트의 절대 경로이고, `data.result_paths`는
정규화된 PDF 이름을 마지막 구성요소로 포함하는 절대 경로 배열이다. result 루트가
없으면 빈 배열을 반환하며 폴더를 만들거나 상태를 변경하지 않는다.

`scan_pdf(work_id, scan_size, force_vision)`는 PDF 메타, 텍스트 품질, 페이지
오프셋, 챕터 경계 추천을 반환한다. 내장 목차가 없거나 `force_vision=true`이면
목차 후보 JPEG만 렌더하며 OCR 모델 준비·로드·실행은 하지 않는다. 공개 응답의
추천과 next step에는 사용자 선택 fallback이 없고, `set_chapters` next step은
`required_parameters=["chapters"]`만 담는다.

`prepare_ocr(work_id)`는 한국어/영어 Elicitation을 열고 승인된 PaddleOCR CPU
모델을 준비해 언어를 상태에 보존한다. 성공 응답은 캐시 경로, 모델별 캐시 여부,
다운로드 필요 여부, 모델 로드 여부, 소요 시간을 담는다.

`scan_toc_with_ocr(work_id)`는 목차 페이지 JPEG를 준비된 OCR 언어로 읽어
`ocr_text`, `ocr_error`, `ocr_status`를 갱신한다. 언어나 모델 캐시가 없으면
`prepare_ocr(work_id)`를 안내한다. 성공 next step은 `chapters`만 요구하는
`set_chapters` 계약이다.

`set_chapters(work_id, chapters, book_info)`는 챕터 구성 방식, text/OCR,
sequential/parallel을 순차 Elicitation으로 확인하고 모두 승인된 뒤에만 처리
상태를 변경한다. 직접 입력을 선택하면 페이지 번호 기준과 챕터별 범위를 받는 form을,
균등 청크를 선택하면 기본 분할 크기를 수정할 수 있는 정수 form을 중간에 추가한다.
첫 번째 form은 `source_pages`가 있는 챕터를
`PDF p.N–M · 원문 p.A–B` 형식으로 표시하며, 명시적 `null`은 오프셋 상태에 따라
`원문 페이지 미상` 또는 `원문 페이지 없음`으로 표시한다. `message`에는 이 챕터
목록만 두고 구성 전략 선택지는 `챕터 구성 방식` 필드에 설명형 enum으로 표시한다.
별도 `chapters_confirmed` boolean은 사용하지 않는다. 직접 입력한 `pdf_pages`는 추출
범위가 되고, 원문 페이지 기준 입력은 확인된 오프셋으로 PDF 페이지로 변환한다.
text 품질이 신뢰 불가면 추출 form은 OCR만 허용하고 그 이유를 message에 표시한다.
`pdf_pages=[start,end]`는 필수 1-based inclusive 범위이고 `source_pages`는 선택적
표시 메타다. 입력 전체를 무부작용으로 검증한 뒤 모드·챕터·phase를 한 잠금 구간에서
확정한다. OCR은 `prepare_ocr`에 저장된 언어를 사용하며 모델 캐시가 없으면
`prepare_ocr(work_id)`를 안내한다. 본문 실패는 새 설정과 챕터별 오류를 유지한다.

`get_subagent_prompts(work_id)`는 챕터 설정이 완료된 작업에서만 내용 목록
프롬프트, 요약 프롬프트, 독립 검토 프롬프트, 기본 문제 프롬프트, 호환용 결합
요약자 프롬프트, 확장 문제 프롬프트와 처리 순서를 함께 반환한다. 응답 키는
`content_map_prompt`, `summary_prompt`, `review_prompt`,
`basic_question_prompt`, `summarizer_prompt`, `extension_prompt`다.
`summary_pending_chapter_ids`,
`extension_pending_chapter_ids`를 반환한다. 두 pending 목록은 각각 아직 저장할
요약·기본 문제와 확장 문제가 남은 챕터 ID를 자연 정렬해 담는다. 호환용
`chapter_ids`는 두 목록의 자연 정렬 합집합이며 완료·skip 챕터는 제외한다.
workflow와 `next_action`은 실제 pending 결과 유형만 안내한다. 두 목록이 모두 비면
`list_pending_chapters`를 호출한 뒤 `finalize_study(work_id)`로 진행한다. 요약
pending 챕터는 raw `text`와 `char_count`를 검증한다. 요약은 완료되고 확장 문제만
pending인 챕터는 raw 대신 저장된 `summary`, `key_points`, `source_char_count`를
검증한다.

pending 판정의 정확한 상태 매핑은 다음과 같다.

- `completed`, `skipped` → done: 해당 결과의 pending 목록에서 제외한다.
- `pending`, `failed`, `in_progress` → pending: 해당 결과의 pending 목록에 포함한다.
- 확장 문제가 비활성인 작업 → `extension_pending_chapter_ids=[]`: 각 챕터의 `extension_status`와 무관하게 항상 빈 목록을 반환한다.

`get_chapter_content(work_id, chapter_id)`는 챕터 입력을 반환한다. text 모드와 OCR 모드 모두 `text`가 들어간다. OCR 모드의 `text`는 `set_chapters` 시점에 PaddleOCR CPU로 선계산해 `chapters_raw/chN.json`에 저장한 본문이다. 등록되지 않은 `chapter_id`, skip 챕터, 아직 챕터가 설정되지 않은 작업은 실패한다.

`get_chapter_summary(work_id, chapter_id)`는 완료·저장된 `summary`,
`key_points`, 원문 문제 개수 상한 계산용 `source_char_count`, `chapter_id`와
선택적 `title` 식별 메타를 반환한다. 원문 `text`, `content_map`, 검토 내부 정보는
문제 생성 입력으로 반환하지 않는다.
요약이 completed가 아니거나 저장 요약의 필수값이 비어 있으면 상태 변경 없이
실패하며, 성공하면 확장 문제 상태만 `in_progress`로 표시한다.

`save_chapter_result(work_id, chapter_id, data)`는 요약과 기본 문제를 저장한다.
`summary`는 비어 있지 않은 문자열, `key_points`는 비어 있지 않은 문자열 배열이어야
한다. 또한 전체 챕터에서 만든 `content_map`과 `summary_review`가 필수다.
`content_map.sections`는 비어 있지 않아야 하고 각 section은 유일하고 안전한 `id`,
비어 있지 않은 `heading`, `explicit_subchapter` boolean, 하나 이상의
`important_points`를 가진다. important point는 유일한 `id`, `content`,
`significance`를 가진다. 서브 챕터가 없으면 챕터 전체 section 하나를 사용하고,
있으면 모든 명시적 서브 챕터를 section으로 만들며 각 heading이 최종 Markdown
summary에 나타나야 한다. `summary_review.status`는 `passed`여야 하고,
`covered_section_ids`와 `covered_point_ids`는 content map의 전체 id와 정확히
일치해야 하며 `missing_significant_content`와 `distortions`는 빈 배열이어야 한다.
요약 품질에 고정 글자 수나 압축률 제한은 적용하지 않는다.

`questions`는 객체여야 하며 `multiple_choice`, `short_answer`, `reflection` 키를 모두 배열로 가져야 한다. 활성화된 기본 문제 유형은 빈 배열이면 실패하고, 비활성화된 유형도 키는 유지해야 한다. 객관식은 agent 입력으로 비어 있지 않은 `id`, `question`, `explanation`, 하나의 `correct_answer`, 하나 이상의 비어 있지 않고 중복되지 않은 `incorrect_answers`를 받을 수 있다. 서버는 성공적으로 저장할 때만 정답·오답을 한 번 섞어 저장 형식의 최소 2개 비어 있지 않은 `options`와 범위 안의 정수 `answer_index`로 바꾸며, 이후 렌더·재개·재최종화는 저장된 순서를 바꾸지 않는다. 호환을 위해 agent는 기존 저장 형식인 `options`와 `answer_index`를 직접 보내도 되며, 이 경우 순서는 바꾸지 않는다. 단답형과 성찰형 항목은 비어 있지 않은 `id`, `question`, `model_answer`를 가져야 한다. 문제 ID는 영문자·숫자·`_`·`-`만 쓸 수 있고, 기본·확장 문제를 합친 같은 챕터 안에서 유일해야 한다. 각 유형의 개수는 raw 본문의 `char_count`별 최대치(3,000 미만: 3/1/1/1, 3,000–9,999: 5/2/2/1, 10,000–24,999: 7/3/2/2, 25,000 이상: 10/4/3/3; 객관식/단답형/주관식/확장형)를 넘을 수 없다. `chapter_id`가 payload에 있으면 요청 `chapter_id`와 같아야 하고, `title`이 있으면 문자열이어야 한다. 실패하면 `data.missing`에 `content_map`, `summary_review.status`, `summary_review.covered_point_ids`, `questions.multiple_choice[0].correct_answer`, `questions.multiple_choice[0].incorrect_answers`, `questions.multiple_choice[0].options`, `questions.multiple_choice[0].id`, `work_id`, `chapter_id`, `state` 같은 경로를 담고, 해당 챕터를 completed로 바꾸거나 요약·퀴즈 파일을 남기면 안 된다. `body_text`는 요구하지 않으며, 들어오더라도 저장 전에 제거되어 `chapters_raw`의 canonical `text`와 `char_count`를 덮어쓰지 않는다.

`save_extension_result(work_id, chapter_id, data)`는 외부 검색 없이 `get_chapter_summary`가 반환한 요약과 학습자 정보로 만든 확장 문제를 저장한다. 확장형이 비활성인 작업은 실패한다. `questions.extension`은 비어 있지 않은 배열이어야 하고, 각 항목은 비어 있지 않은 `id`, `question`, `model_answer`를 가져야 한다. ID 문자·챕터 전체 유일성·본문 글자 수별 최대 개수 규칙은 `save_chapter_result`와 같다. 저장 스키마에 없는 추가 필드는 제거한다. `chapter_id`가 payload에 있으면 요청 `chapter_id`와 같아야 한다. 실패하면 `data.missing`에 필드 경로나 `work_id`, `chapter_id`, `state`를 담고, 해당 챕터의 extension 상태를 completed로 바꾸거나 확장 문제 파일을 남기면 안 된다. `body_text`가 들어오면 저장 전에 제거된다.

`get_work_state(work_id)`는 상태 파일 전체를 반환한다. 알 수 없는 작업은 실패한다.

`list_pending_chapters(work_id)`는 완료되지 않은 요약과 확장 챕터 ID를 반환한다.
챕터 설정이 완료되고 두 pending 목록이 모두 비면
`data.next_step={"tool":"finalize_study","required_parameters":[]}`를 반환한다.

`finalize_study(work_id)`는 HTML/Markdown+TUI Elicitation을 열고 승인된 형식으로
최종 결과물을 만든다. `.work`는 항상 보존한다. 미완료 결과가 있어도 완료분만
렌더하며 `data.omitted_chapters`와 `next_action`이 미반영 결과를 알린다.
Form에는 `html`, `md+tui`로 표시하지만 승인값은 기존 내부 형식인 `html`,
`md_tui`로 변환해 저장·렌더링한다.

`cleanup_work(work_id)`는 삭제 Elicitation 승인 뒤 rendering phase가 완료된 작업의
정확한 `.work`만 삭제한다. 결과 파일, manifest, 진도, 사용자 파일은 건드리지 않고
렌더링도 다시 실행하지 않는다.

공개 도구 설명, 오류와 `next_action`의 호출 예시는 등록된 MCP 입력만 사용할 수
있다. 선택값이 필요한 경우에는 해당 도구가 form Elicitation을 연다고 안내하고,
제거된 선택 인자나 번호형 자유 텍스트 선택지를 대체 입력처럼 노출하지 않는다.

렌더러는 임시 staging 폴더에 완전한 새 세대를 만든 뒤 이전 manifest의 관리 경로만 교체한다. 렌더 또는 설치가 실패하면 이전 결과와 manifest를 복원하고 partial 파일을 최종 폴더에 남기지 않는다. manifest가 관리하지 않는 기존 경로와 새 렌더 경로가 충돌하면 사용자 파일을 덮어쓰지 않고 실패한다.

## 출력물 계약

HTML 출력은 다중 챕터일 때 `index.html`과 `chN.html`, 단일 챕터일 때 `main.html`을 만든다. `assets/`, `study_html.py`, `start_study.sh`, `start_study.bat`, `README.md`가 함께 복사된다. 생성 완료 HTML 응답은 기존 `launch_command`, `python`, `entry_page`, 고정 포트의 `default_url`을 유지하고, `launch_scripts={"macos_linux":"start_study.sh","windows":"start_study.bat"}`, `auto_port_on_script_launch=true`를 추가한다. 사용자는 같은 컴퓨터의 자료를 생성한 프로젝트 환경에서 플랫폼별 스크립트를 더블클릭해 실행한다. 스크립트는 사용 가능한 포트를 자동 배정하고 loopback 전용 서버를 열어 브라우저를 시작한다. 문제가 있으면 기존 직접 실행 경로 `study_html.py --port 8765`를 계속 사용할 수 있다. 진도 저장은 변함없이 `study_html.py`가 제공하는 로컬 progress API와 `progress/` 아래 JSON을 사용한다.

Markdown+TUI 출력은 `book.md`, 루트 `study_tui.py`, 챕터별 `summary.md`, `quiz.json`, 챕터별 launcher를 만든다. 진도는 각 챕터 폴더의 `progress.json`에 저장된다.

Markdown+TUI에서 `progress.json.answers`가 비어 있지 않고 `completed=false`이면, 챕터 launcher와 루트 TUI는 선택 메뉴를 먼저 묻지 않고 문제 순서상 첫 미응답 문제부터 자동으로 이어서 푼다. 저장된 답안이 있는 문제는 다시 묻지 않는다.

두 출력 형식은 같은 저장 결과를 읽는다. 같은 `work_id`에서 출력 형식만 바꾸어 다시 `finalize_study`를 호출하면 같은 내용의 다른 표시 형식을 만들 수 있다.

두 출력 형식의 챕터 페이지 표기는 같은 규칙을 사용한다. `source_pages`가 배열이면 `PDF p.N–M · 원문 p.A–B`, 명시적 `null`이면서 오프셋을 모르면 `원문 페이지 미상`, 오프셋을 알면 번호가 없는 앞부분으로 보아 `원문 페이지 없음`을 표시한다. `source_pages` 입력 자체가 없으면 `PDF p.N–M`만 표시한다.

최종 폴더의 `.pdf-learner-manifest.json`은 현재 렌더 형식, 학습 fingerprint, 서버가 관리하는 top-level 경로를 기록한다. 재렌더할 때 이전 형식의 파일과 사라진 챕터 파일은 manifest 범위 안에서 제거된다. 진도는 출력 형식과 학습 fingerprint가 모두 같을 때만 새 세대로 복사된다. 형식, 챕터, 문제 옵션, 요약 또는 문제 내용이 바뀌면 이전 진도를 재사용하지 않는다.
