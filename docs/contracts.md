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

## 도구 흐름

`init_work(pdf_path, output_dir, enable_*, user_context)`는 작업 폴더를 만든다. 입력 PDF가 없거나 모든 문제 유형이 꺼져 있으면 실패한다. 성공 응답은 `work_id`, `.work` 경로, 실제 출력 경로를 담고 `scan_pdf`를 다음 단계로 안내한다.

`resume_work(output_dir, pdf_path)`는 서버 재시작 후 디스크의 `.work/state.json`을 다시 등록한다. `output_dir`과 `pdf_path`가 모두 없거나, 대상 폴더에 상태 파일이 없으면 실패한다. 성공 응답은 남은 요약·확장 챕터 목록을 담는다.

`scan_pdf(work_id, scan_size, force_vision)`는 PDF 메타, 텍스트 품질, 언어, 페이지 오프셋, 챕터 경계 추천을 반환한다. 내장 목차가 있으면 `recommendations.suggested_chapters`에 물리 페이지 범위가 들어간다. 내장 목차가 없거나 `force_vision=true`이면 `toc_page_images`를 반환한다. 각 항목은 목차 페이지 JPEG 경로와 `ocr_status="not_started"`, 빈 `ocr_text`, `ocr_error=null`을 담는다. `scan_pdf`는 PaddleOCR 모델 다운로드, 모델 로드, OCR 실행을 하지 않는다. `force_vision`은 외부 계약 호환을 위한 기존 파라미터명이다. 알 수 없는 `work_id`나 손상된 PDF는 실패한다.

`prepare_ocr(work_id)`는 PaddleOCR CPU 모델을 준비한다. 모델 캐시가 없으면 이 단계에서 다운로드와 로드가 발생할 수 있다. 성공 응답은 캐시 경로, 모델별 캐시 여부, 다운로드 필요 여부, 모델 로드 여부, 소요 시간을 담는다. 이 도구는 PDF 본문이나 목차 이미지를 OCR하지 않는다.

`scan_toc_with_ocr(work_id)`는 `scan_pdf`가 렌더한 목차 페이지 JPEG를 PaddleOCR CPU로 읽어 `toc_page_images[].ocr_text`, `ocr_error`, `ocr_status`를 갱신해 반환한다. 모델 캐시가 없으면 OCR을 시작하지 않고 `ok=false`, `next_action=prepare_ocr(...)`로 복구 방법을 안내한다. 모델 캐시가 있으면 내부 모델 로드는 허용한다. 일부 목차 페이지 OCR 실패는 도구 전체 실패가 아니라 해당 항목의 `ocr_error`로 표현된다. 클라이언트는 서버가 제공한 OCR 텍스트와 필요 시 이미지를 확인해 챕터를 구성해야 한다.

`set_chapters(work_id, chapters, execution_mode, extraction_mode, book_info, language)`는 챕터와 처리 모드를 확정한다. `execution_mode`는 `sequential` 또는 `parallel`, `extraction_mode`는 `text` 또는 `ocr`만 허용한다. 둘 중 하나가 빠지면 실패 응답의 `data.choices`를 사용자에게 그대로 보여줘야 한다. 페이지 범위가 문서 밖이거나 챕터 ID가 중복되면 실패한다. OCR 모드에서 모델 캐시가 없으면 본문 OCR을 시작하지 않고 `ok=false`, `next_action=prepare_ocr(...)`로 복구 방법을 안내한다. 모델 캐시가 있으면 내부 모델 로드는 허용한다. OCR 모드 본문 선처리 중 실패한 챕터가 있으면 `ok=false`, `next_action=null`이며, `data.failed_chapters`에 `{chapter_id, failed_pages, error}`를 담는다. 이미 같은 `page_range`의 유효한 OCR `chapters_raw`가 있으면 재OCR하지 않고 저장된 `text`와 `char_count`를 재사용한다. OCR 선처리 병렬 상한은 서버 프로세스 전역으로 공유된다.

처리 모드 선택 실패 응답의 각 선택지는 `execution_mode`, `extraction_mode`, `label`, `desc`를 담는다. 텍스트 레이어가 없거나 깨진 PDF에서는 `forced_extraction_mode="ocr"`가 함께 오고, 선택지는 OCR 조합만 남는다. 클라이언트는 빠진 text 선택지를 다시 만들어 사용자에게 보여주면 안 된다.

`get_subagent_prompts(work_id)`는 요약자 프롬프트, 확장 문제 프롬프트, 처리 순서, 본문 챕터 ID 목록을 반환한다. skip 챕터는 `chapter_ids`에서 제외되고 `skipped_chapter_ids`에 따로 들어간다. non-skip 챕터의 raw 본문 파일이 없거나 `text`가 비어 있거나 `char_count`가 실제 길이와 맞지 않으면 실패하며, `data.invalid_chapters`에 챕터별 사유를 담는다. 남아 있는 OCR 실패는 `data.failed_chapters`에도 같은 `{chapter_id, failed_pages, error}` 형태로 노출한다.

`get_chapter_content(work_id, chapter_id)`는 챕터 입력을 반환한다. text 모드와 OCR 모드 모두 `text`가 들어간다. OCR 모드의 `text`는 `set_chapters` 시점에 PaddleOCR CPU로 선계산해 `chapters_raw/chN.json`에 저장한 본문이다. 등록되지 않은 `chapter_id`, skip 챕터, 아직 챕터가 설정되지 않은 작업은 실패한다.

`save_chapter_result(work_id, chapter_id, data)`는 요약과 기본 문제를 저장한다. `summary`, `key_points`, 활성화된 `multiple_choice`, `short_answer`, `reflection` 중 필요한 값이 비어 있으면 실패하고 `data.missing`에 누락 필드를 담는다. `body_text`는 요구하지 않으며, 들어오더라도 저장 전에 제거되어 `chapters_raw`의 canonical `text`와 `char_count`를 덮어쓰지 않는다.

`search_extension_context(work_id, chapter_id, query)`는 확장 문제용 검색 결과를 반환한다. 빈 검색어는 실패한다. 외부 검색 자체의 오류는 `ok=true`, `data.exa_ok=false`, `data.results=[]`로 표현해 챕터 처리를 계속하게 한다.

`save_extension_result(work_id, chapter_id, data)`는 확장 문제를 저장한다. `questions.extension`이 비어 있으면 실패한다.

`get_work_state(work_id)`는 상태 파일 전체를 반환한다. 알 수 없는 작업은 실패한다.

`list_pending_chapters(work_id)`는 완료되지 않은 요약과 확장 챕터 ID를 반환한다. skip과 completed는 남은 작업으로 보지 않는다.

`finalize_study(work_id, output_format, keep_work_dir, force)`는 최종 결과물을 만든다. `output_format`은 `html` 또는 `md_tui`만 허용한다. 값이 없으면 실패 응답의 `data.choices`를 사용자에게 그대로 보여줘야 한다. 남은 챕터가 있으면 `force=true`가 아닌 한 실패한다.

출력 형식 선택 실패 응답의 선택지는 `value`, `label`, `desc`를 담는다. 클라이언트는 `html`과 `md_tui` 외의 값을 만들어 제시하면 안 된다.

## 출력물 계약

HTML 출력은 다중 챕터일 때 `index.html`과 `chN.html`, 단일 챕터일 때 `main.html`을 만든다. `assets/`, `study_html.py`, `README.md`가 함께 복사된다. 진도 저장은 `study_html.py`가 제공하는 로컬 progress API를 통해 이뤄진다.

Markdown+TUI 출력은 `book.md`, 루트 `study_tui.py`, 챕터별 `summary.md`, `quiz.json`, 챕터별 launcher를 만든다. 진도는 각 챕터 폴더의 `progress.json`에 저장된다.

두 출력 형식은 같은 저장 결과를 읽는다. 같은 `work_id`에서 출력 형식만 바꾸어 다시 `finalize_study`를 호출하면 같은 내용의 다른 표시 형식을 만들 수 있다.
