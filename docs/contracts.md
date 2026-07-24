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

성공이면 `ok=true`, 실패면 `ok=false`다. 실패 응답도 `data`에 복구용 선택지나 누락 목록을 담을 수 있다. 클라이언트는 예외 대신 이 봉투를 보고 다음 단계를 정해야 한다.

성공 응답에서 다음 도구에 사용자 선택이 필요하면 `data.next_step`은 `tool`,
`required_parameters`, `choices`를 담는다. `choices`는 서버가 제공한 항목과 설명을
그대로 사용자에게 보여준 뒤 선택값만 다음 도구에 전달한다. `next_step`이 없는 성공
응답은 새 사용자 선택이 필요 없다는 뜻이다. 실패 응답의 `data.choices`는 누락·오류
호출을 고치기 위한 같은 선택지의 fallback이며, 정상 선택지 조회를 위해 일부러 실패
호출을 할 필요는 없다.

## 도구 흐름

`init_work(pdf_path, output_dir, enable_multiple_choice, enable_short_answer, enable_reflection, enable_extension, user_context, replace_existing)`는 작업 폴더를 만든다. 객관식은 기존 호환을 위해 기본 활성이다. 단답형·주관식·확장형은 기본값이 없으며, 사용자가 이미 명시하지 않은 값은 `null`로 저장한다. 입력 PDF가 없거나 모든 문제 유형을 명시적으로 끄면 실패한다. 성공 응답은 `work_id`, `.work` 경로, 실제 출력 경로, 현재 `question_options`, `question_setup`을 담는다. `output_dir`이 비어 있으면 MCP 서버를 시작한 디렉터리 아래 `result/<pdf_basename>`을 쓴다. 호출 측 cwd 아래에 만들려면 클라이언트는 `<호출 측 cwd>/result/<pdf_basename>`을 `output_dir`로 명시해야 한다.

같은 `output_dir`에 기존 pdf-study 작업이 있으면 `init_work`는 상태나 파일을 바꾸지 않고 `ok=false`와 `data.existing_work`, `data.choices`를 반환한다. 재개 가능한 `.work/state.json`이 있으면 선택지는 다음 세 항목이다. 항목과 설명은 클라이언트가 바꾸거나 합치거나 추천을 붙이지 않고 그대로 보여줘야 한다.

- `resume` / `기존 작업 이어가기`: `resume_work(output_dir=...)`로 기존 상태를 등록한다.
- `replace` / `기존 작업 교체`: 같은 인자로 `init_work(..., replace_existing=true)`를 다시 호출한다.
- `new_output_dir` / `새 출력 폴더 사용`: 사용자가 정한 다른 `output_dir`로 `init_work`를 호출한다.

`.work`가 없는 완료 결과나 손상된 작업은 재개할 수 없으므로 `replace`, `new_output_dir`만 반환한다. pdf-study 관리 흔적이 없는 비어 있지 않은 폴더는 사용자 파일을 보호하기 위해 `new_output_dir`만 반환한다. `replace_existing=true`는 새 입력의 PDF 경로·문제 유형·학습자 정보 검증이 모두 끝난 뒤 기존 `.work`만 제거한다. 이전 렌더 결과와 manifest는 새 렌더가 성공할 때까지 유지한다.

`question_setup.questions`는 미정인 문제 유형마다 `field`, `question`, `choices`를 담는다. 각 선택지는 `value`, `label`, `desc`를 가지며 클라이언트는 이를 바꾸거나 합치거나 추천을 붙이지 않고 그대로 보여줘야 한다. `question_setup.user_context_request`는 선택 입력인 학습 목적, 배경지식, 관심 분야, 현재 수준을 안내한다. 이미 학습자 정보가 있으면 이 값은 `null`이다. 클라이언트는 선택과 학습자 응답을 받은 뒤 `scan_pdf`에 전달한다.

`resume_work(output_dir, pdf_path)`는 서버 재시작 후 디스크의 `.work/state.json`을 다시 등록한다. `output_dir`과 `pdf_path`가 모두 없거나, 대상 폴더에 상태 파일이 없으면 실패한다. 문제 유형 선택이 아직 미정이면 성공 응답에 `question_setup`을 담아 사용자 선택부터 이어가게 한다. 선택이 끝난 작업은 남은 요약·확장 챕터 목록을 담으며, 챕터 설정이 완료되고 두 목록이 비었을 때만 `data.next_step`에 `finalize_study`의 출력 형식 선택지를 넣는다.

`scan_pdf(work_id, scan_size, force_vision, enable_short_answer, enable_reflection, enable_extension, user_context)`는 미정인 문제 유형 선택을 먼저 확정한 뒤 PDF 메타, 텍스트 품질, 페이지 오프셋, 챕터 경계 추천을 반환한다. 미정인 선택이 하나라도 전달되지 않았거나 boolean이 아니면 상태를 바꾸거나 PDF를 스캔하지 않고 `ok=false`와 같은 `question_setup`을 반환한다. 선택적 `user_context`는 앞뒤 공백을 제거해 상태에 저장한다. 한번 확정된 문제 유형을 이후 재스캔에서 다른 값으로 조용히 바꿀 수 없다. 내장 목차가 있으면 `recommendations.suggested_chapters`의 각 챕터에 PDF 범위 `pdf_pages`와 선택적 원문 번호 `source_pages`가 들어간다. 정상 텍스트 레이어에서는 반복 footer로 확인된 high 신뢰도 오프셋만 `page_offset`으로 사용한다. 텍스트가 깨졌거나 없으면 이 단계에서 오프셋을 계산하지 않으며, 목차 이미지 OCR 뒤에 다시 판단한다. low 후보는 `page_offset_candidate` 진단값으로만 노출하고 원문 페이지·범위 계산에는 쓰지 않는다. 전체 범위 메타는 `pdf_pages_available`과 `source_pages_available`이다. 챕터 구성 선택지는 `recommendations.user_choice_options`의 `{value,label,desc}` 배열이 canonical이며, 기존 문자열 배열 `user_choices`는 읽기 호환용으로 유지한다. `data.set_chapters_next_step`은 이후 `set_chapters`에 필요한 `chapters`, `execution_mode`, `extraction_mode`와 유효한 처리 모드 조합을 항상 제공한다. 내장 목차 흐름에서는 같은 객체가 즉시 `data.next_step`에도 들어가며, 목차 이미지 흐름의 즉시 `next_step`은 `prepare_ocr`이고 처리 모드 정보는 OCR 뒤까지 `set_chapters_next_step`에 보존된다. 텍스트 레이어가 없거나 깨진 PDF에서는 이 조합에 OCR 두 가지밖에 들어가지 않는다. 내장 목차가 없거나 `force_vision=true`이면 `toc_page_images`를 반환한다. 각 항목은 목차 페이지 JPEG 경로와 `ocr_status="not_started"`, 빈 `ocr_text`, `ocr_error=null`을 담는다. `scan_pdf`는 PaddleOCR 모델 다운로드, 모델 로드, OCR 실행을 하지 않는다. `force_vision`은 외부 계약 호환을 위한 기존 파라미터명이다. 알 수 없는 `work_id`나 손상된 PDF는 실패한다.

OCR이 필요한 목차 이미지 흐름 또는 텍스트 레이어가 없거나 깨진 PDF에서는 성공 응답에 `ocr_language_setup={field,question,choices}`가 들어가며 choices는 `korean`, `english` 두 항목뿐이다. 이때 `data.next_step`은 `prepare_ocr`와 필수 `ocr_language`를 반환한다. 클라이언트는 선택지를 바꾸지 않고 사용자 선택값을 전달해야 한다.

`prepare_ocr(work_id, ocr_language)`는 사용자가 고른 `korean` 또는 `english` PaddleOCR CPU 모델을 준비하고 성공 시 작업 상태에 보존한다. 다른 값은 거부하고 같은 선택지를 반환한다. 모델 캐시가 없으면 이 단계에서 다운로드와 로드가 발생할 수 있다. 성공 응답은 캐시 경로, 모델별 캐시 여부, 다운로드 필요 여부, 모델 로드 여부, 소요 시간을 담는다. 이 도구는 PDF 본문이나 목차 이미지를 OCR하지 않는다.

`scan_toc_with_ocr(work_id)`는 `scan_pdf`가 렌더한 목차 페이지 JPEG를 작업에 보존된 한국어 또는 영어 PaddleOCR CPU 모델로 읽어 `toc_page_images[].ocr_text`, `ocr_error`, `ocr_status`를 갱신해 반환한다. 언어를 고르지 않았거나 모델 캐시가 없으면 OCR을 시작하지 않고 `ok=false`, `next_action=prepare_ocr(...)`로 복구 방법을 안내한다. 모델 캐시가 있으면 내부 모델 로드는 허용한다. 일부 목차 페이지 OCR 실패는 도구 전체 실패가 아니라 해당 항목의 `ocr_error`로 표현된다. 완료된 OCR 텍스트의 반복 footer 번호가 같은 high 신뢰도 오프셋을 지지하면 이 도구가 `page_offset`과 추천의 `source_pages`를 갱신한다. low 후보는 `page_offset_candidate`로만 반환한다. 성공 응답의 `data.next_step`은 사용자가 구성한 `chapters`와 처리 모드 조합을 받는 `set_chapters` 계약이다. 클라이언트는 서버가 제공한 OCR 텍스트와 필요 시 이미지를 확인해 챕터를 구성해야 한다.

`set_chapters(work_id, chapters, execution_mode, extraction_mode, book_info)`는 챕터와 처리 모드를 확정한다. 각 챕터의 `pdf_pages=[start,end]`는 필수이며 PDF 파일의 1-based inclusive 범위다. `source_pages=[start,end]` 또는 명시적 `null`은 선택적 표시 메타로 상태, raw, 성공 응답에 그대로 보존된다. 구형 클라이언트와 기존 `.work`의 `page_range`·`printed_range`는 입력과 읽기에서만 각각 새 키로 정규화하며, 새 응답과 저장에는 구형 키를 쓰지 않는다. `execution_mode`는 `sequential` 또는 `parallel`, `extraction_mode`는 `text` 또는 `ocr`만 허용한다. 둘 중 하나가 빠지면 실패 응답의 `data.choices`를 사용자에게 그대로 보여줘야 한다. 스캔이 끝나지 않았거나 `pdf_pages`가 문서 밖이거나 챕터 ID가 중복되는 등 입력 검증이 실패하면 모드, 기존 챕터 진행 상태와 책 정보를 바꾸지 않는다. 검증된 모드와 챕터는 `chapter_setup=completed`, `chapter_processing=in_progress`와 함께 한 번에 확정한다. 이후 본문 준비가 모두 성공하면 처리 phase는 `completed`, 추출이나 OCR 실패가 있으면 `failed`가 되며 새 챕터 설정과 챕터별 오류는 재시도를 위해 유지한다. 같은 `work_id`의 `set_chapters` 호출은 setup 확정부터 본문 준비와 phase 종결까지 직렬화된다. state 저장 실패 뒤 책 정보 복원이 실패하면 성공이나 원래 입력 오류로 가장하지 않고 transaction 오류를 반환한다. OCR 모드에서 모델 캐시가 없으면 본문 OCR을 시작하지 않고 `ok=false`, `next_action=prepare_ocr(...)`로 복구 방법을 안내한다. 모델 캐시가 있으면 내부 모델 로드는 허용한다. OCR 모드 본문 선처리 중 실패한 챕터가 있으면 `ok=false`, `next_action=null`이며, `data.failed_chapters`에 `{chapter_id, failed_pages, error}`를 담는다. 이미 같은 `pdf_pages`의 유효한 OCR `chapters_raw`가 있으면 재OCR하지 않고 저장된 `text`와 `char_count`를 재사용한다. OCR 선처리 병렬 상한은 서버 프로세스 전역으로 공유된다.

`set_chapters`의 OCR 모드는 선택한 `ocr_language` 또는 `prepare_ocr`가 상태에 보존한 언어를 사용한다. 둘 다 없으면 상태를 바꾸지 않고 한국어·영어 선택지를 반환한다. OCR raw에는 사용 언어를 함께 기록하므로 다른 언어로 다시 설정하면 기존 raw를 재사용하지 않는다.

처리 모드 선택은 `scan_pdf` 또는 `scan_toc_with_ocr`의 `data.next_step.choices`에서 먼저 받는다. 각 선택지는 `execution_mode`, `extraction_mode`, `label`, `desc`를 담는다. 누락·오류로 `set_chapters`가 실패하면 같은 선택지가 `data.choices`에 fallback으로 들어간다. 텍스트 레이어가 없거나 깨진 PDF에서는 `forced_extraction_mode="ocr"`가 함께 오고, 선택지는 OCR 조합만 남는다. 클라이언트는 빠진 text 선택지를 다시 만들어 사용자에게 보여주면 안 된다.

`get_subagent_prompts(work_id)`는 요약자 프롬프트, 확장 문제 프롬프트, 처리 순서와 함께 `summary_pending_chapter_ids`, `extension_pending_chapter_ids`를 반환한다. 두 목록은 각각 아직 저장할 요약·기본 문제와 확장 문제가 남은 챕터 ID를 자연 정렬해 담는다. 기존 클라이언트용 `chapter_ids`는 두 pending 목록의 자연 정렬 합집합이며 완료된 챕터는 포함하지 않는다. skip 챕터는 모든 처리 목록에서 제외되고 `skipped_chapter_ids`에 따로 들어간다. workflow와 성공 `next_action`은 각 목록에 실제로 남은 결과 유형만 생성·저장하도록 안내하며, 두 pending 목록이 모두 비면 렌더링으로 진행하게 한다. raw 본문 파일, 비어 있지 않은 `text`, 실제 길이와 같은 `char_count` 검증은 두 pending 목록의 합집합에만 적용하므로 완료 챕터의 raw 손상은 재개를 막지 않는다. pending raw가 유효하지 않으면 실패하고 `data.invalid_chapters`에 챕터별 사유를 담으며, 그중 남아 있는 OCR 실패는 `data.failed_chapters`에도 같은 `{chapter_id, failed_pages, error}` 형태로 노출한다.

pending 판정의 정확한 상태 매핑은 다음과 같다.

- `completed`, `skipped` → done: 해당 결과의 pending 목록에서 제외한다.
- `pending`, `failed`, `in_progress` → pending: 해당 결과의 pending 목록에 포함한다.
- 확장 문제가 비활성인 작업 → `extension_pending_chapter_ids=[]`: 각 챕터의 `extension_status`와 무관하게 항상 빈 목록을 반환한다.

`get_chapter_content(work_id, chapter_id)`는 챕터 입력을 반환한다. text 모드와 OCR 모드 모두 `text`가 들어간다. OCR 모드의 `text`는 `set_chapters` 시점에 PaddleOCR CPU로 선계산해 `chapters_raw/chN.json`에 저장한 본문이다. 등록되지 않은 `chapter_id`, skip 챕터, 아직 챕터가 설정되지 않은 작업은 실패한다.

`save_chapter_result(work_id, chapter_id, data)`는 요약과 기본 문제를 저장한다. `summary`는 비어 있지 않은 문자열, `key_points`는 비어 있지 않은 문자열 배열이어야 한다. `questions`는 객체여야 하며 `multiple_choice`, `short_answer`, `reflection` 키를 모두 배열로 가져야 한다. 활성화된 기본 문제 유형은 빈 배열이면 실패하고, 비활성화된 유형도 키는 유지해야 한다. 객관식 항목은 비어 있지 않은 `id`, `question`, `explanation`, 최소 2개의 비어 있지 않은 `options`, 범위 안의 정수 `answer_index`를 가져야 한다. 단답형과 성찰형 항목은 비어 있지 않은 `id`, `question`, `model_answer`를 가져야 한다. 문제 ID는 영문자·숫자·`_`·`-`만 쓸 수 있고, 기본·확장 문제를 합친 같은 챕터 안에서 유일해야 한다. 각 유형의 개수는 raw 본문의 `char_count`별 최대치(3,000 미만: 3/1/1/1, 3,000–9,999: 5/2/2/1, 10,000–24,999: 7/3/2/2, 25,000 이상: 10/4/3/3; 객관식/단답형/주관식/확장형)를 넘을 수 없다. `chapter_id`가 payload에 있으면 요청 `chapter_id`와 같아야 하고, `title`이 있으면 문자열이어야 한다. 실패하면 `data.missing`에 `questions.multiple_choice[0].options`, `questions.multiple_choice[0].id`, `work_id`, `chapter_id`, `state` 같은 경로를 담고, 해당 챕터를 completed로 바꾸거나 요약·퀴즈 파일을 남기면 안 된다. `body_text`는 요구하지 않으며, 들어오더라도 저장 전에 제거되어 `chapters_raw`의 canonical `text`와 `char_count`를 덮어쓰지 않는다.

`save_extension_result(work_id, chapter_id, data)`는 외부 검색 없이 챕터 본문과 학습자 정보로 만든 확장 문제를 저장한다. 확장형이 비활성인 작업은 실패한다. `questions.extension`은 비어 있지 않은 배열이어야 하고, 각 항목은 비어 있지 않은 `id`, `question`, `model_answer`를 가져야 한다. ID 문자·챕터 전체 유일성·본문 글자 수별 최대 개수 규칙은 `save_chapter_result`와 같다. 저장 스키마에 없는 추가 필드는 제거한다. `chapter_id`가 payload에 있으면 요청 `chapter_id`와 같아야 한다. 실패하면 `data.missing`에 필드 경로나 `work_id`, `chapter_id`, `state`를 담고, 해당 챕터의 extension 상태를 completed로 바꾸거나 확장 문제 파일을 남기면 안 된다. `body_text`가 들어오면 저장 전에 제거된다.

`get_work_state(work_id)`는 상태 파일 전체를 반환한다. 알 수 없는 작업은 실패한다.

`list_pending_chapters(work_id)`는 완료되지 않은 요약과 확장 챕터 ID를 반환한다. save 도구의 검증을 통과해 completed가 된 챕터와 skip 챕터만 남은 작업에서 제외된다. 챕터 설정이 완료되고 두 pending 목록이 모두 비면 `data.next_step`에 `finalize_study`와 필수 `output_format`, `html`·`md_tui`의 구조화된 선택지가 들어간다.

`finalize_study(work_id, output_format, keep_work_dir)`는 최종 결과물을 만든다. `force` 파라미터는 없다. `output_format`은 `html` 또는 `md_tui`만 허용한다. 정상 흐름에서는 완료된 `list_pending_chapters` 또는 `resume_work`의 `data.next_step.choices`를 사용자에게 그대로 보여준다. 값이 없으면 같은 선택지가 실패 응답의 `data.choices`에 fallback으로 들어간다. 미완료 결과가 있어도 완료분만 렌더하며, 현재 상태가 `completed`가 아닌 챕터의 예전 요약·문제 JSON은 읽지 않는다. 이 경우 성공 응답의 `data.omitted_chapters`는 각 미반영 챕터의 `{chapter_id, results:[{type, status, error}]}`를 담고, `next_action`도 같은 누락을 사용자에게 알린다. `keep_work_dir=false`면 최초 렌더가 성공한 뒤 `.work`를 함께 지우며, 보존된 작업은 성공 응답의 `data.cleanup_work` 계약으로 나중에 렌더 없이 정리할 수 있다.

`cleanup_work(work_id)`는 `finalize_study`로 rendering phase가 완료된 작업의 정확한 `.work`만 삭제한다. 결과 파일, manifest, 진도, 사용자 파일은 건드리지 않고 렌더링도 다시 실행하지 않는다. 최종 렌더가 끝나지 않은 작업은 재개 데이터를 보호하기 위해 실패한다. 성공하면 해당 work_id의 메모리 등록도 제거하므로 같은 서버 프로세스에서 다시 작업하려면 새 작업을 시작해야 한다.

렌더러는 임시 staging 폴더에 완전한 새 세대를 만든 뒤 이전 manifest의 관리 경로만 교체한다. 렌더 또는 설치가 실패하면 이전 결과와 manifest를 복원하고 partial 파일을 최종 폴더에 남기지 않는다. manifest가 관리하지 않는 기존 경로와 새 렌더 경로가 충돌하면 사용자 파일을 덮어쓰지 않고 실패한다.

출력 형식 선택 실패 응답의 선택지는 `value`, `label`, `desc`를 담는다. 클라이언트는 `html`과 `md_tui` 외의 값을 만들어 제시하면 안 된다.

## 출력물 계약

HTML 출력은 다중 챕터일 때 `index.html`과 `chN.html`, 단일 챕터일 때 `main.html`을 만든다. `assets/`, `study_html.py`, `start_study.sh`, `start_study.bat`, `README.md`가 함께 복사된다. 생성 완료 HTML 응답은 기존 `launch_command`, `python`, `entry_page`, 고정 포트의 `default_url`을 유지하고, `launch_scripts={"macos_linux":"start_study.sh","windows":"start_study.bat"}`, `auto_port_on_script_launch=true`를 추가한다. 사용자는 같은 컴퓨터의 자료를 생성한 프로젝트 환경에서 플랫폼별 스크립트를 더블클릭해 실행한다. 스크립트는 사용 가능한 포트를 자동 배정하고 loopback 전용 서버를 열어 브라우저를 시작한다. 문제가 있으면 기존 직접 실행 경로 `study_html.py --port 8765`를 계속 사용할 수 있다. 진도 저장은 변함없이 `study_html.py`가 제공하는 로컬 progress API와 `progress/` 아래 JSON을 사용한다.

Markdown+TUI 출력은 `book.md`, 루트 `study_tui.py`, 챕터별 `summary.md`, `quiz.json`, 챕터별 launcher를 만든다. 진도는 각 챕터 폴더의 `progress.json`에 저장된다.

Markdown+TUI에서 `progress.json.answers`가 비어 있지 않고 `completed=false`이면, 챕터 launcher와 루트 TUI는 선택 메뉴를 먼저 묻지 않고 문제 순서상 첫 미응답 문제부터 자동으로 이어서 푼다. 저장된 답안이 있는 문제는 다시 묻지 않는다.

두 출력 형식은 같은 저장 결과를 읽는다. 같은 `work_id`에서 출력 형식만 바꾸어 다시 `finalize_study`를 호출하면 같은 내용의 다른 표시 형식을 만들 수 있다.

두 출력 형식의 챕터 페이지 표기는 같은 규칙을 사용한다. `source_pages`가 배열이면 `PDF p.N–M · 원문 p.A–B`, 명시적 `null`이면서 오프셋을 모르면 `원문 페이지 미상`, 오프셋을 알면 번호가 없는 앞부분으로 보아 `원문 페이지 없음`을 표시한다. `source_pages` 입력 자체가 없으면 `PDF p.N–M`만 표시한다.

최종 폴더의 `.pdf-study-manifest.json`은 현재 렌더 형식, 학습 fingerprint, 서버가 관리하는 top-level 경로를 기록한다. 재렌더할 때 이전 형식의 파일과 사라진 챕터 파일은 manifest 범위 안에서 제거된다. 진도는 출력 형식과 학습 fingerprint가 모두 같을 때만 새 세대로 복사된다. 형식, 챕터, 문제 옵션, 요약 또는 문제 내용이 바뀌면 이전 진도를 재사용하지 않는다.
