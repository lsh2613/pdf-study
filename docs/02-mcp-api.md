# 02. MCP API

## MCP 도구 (12개)

### 도구 시그니처

```python
# output_dir을 비우면 <cwd>/result/<pdf_basename>/ 가 자동 사용된다.
# PDF 이름에서 안전하지 않은 문자는 _ 로 치환된다. 같은 PDF로 재실행하면
# 같은 폴더에 덮어씌워진다.
init_work(
    pdf_path: str,
    output_dir: str = "",
    execution_mode: str = "",            # "sequential" | "parallel" — 기본값 없음.
                                          # 임의 지정 금지, 반드시 사용자에게 물어볼 것.
                                          # 미지정 시 ok=False로 거부.
    extraction_mode: str = "",           # "text" | "ocr" — 기본값 없음. 임의 지정
                                          # 금지, 반드시 사용자에게 물어볼 것. 미지정 거부.
                                          # text: 라이브러리로 텍스트 추출(디지털 PDF).
                                          # ocr: 비전 LLM이 페이지 이미지를 직접 읽음
                                          #      (스캔본·글꼴 깨진 PDF).
    enable_multiple_choice: bool = True,
    enable_short_answer: bool = True,
    enable_reflection: bool = True,
    enable_extension: bool = True,
    user_context: str = ""               # 학습자 정보 (선택)
) -> dict
# 응답 data: {work_id, work_dir, output_dir(실제 사용된 절대 경로)}

# 서버 재시작 등으로 in-memory 레지스트리(work_id→work_dir)가 사라졌을 때,
# 디스크의 <output_dir>/.work/state.json에서 work_id를 복원해 재등록한다.
# output_dir을 비우면 pdf_path로 init_work과 동일하게 default 경로를 추론.
# 응답 data: {work_id, output_dir, current_phase, execution_mode,
#             summary_pending, extension_pending}
resume_work(output_dir: str = "", pdf_path: str = "") -> dict

# 페이지 오프셋: data.recommendations에 page_offset(물리=책+offset),
# offset_confidence(high/low/none), 각 suggested_chapter의 page_range(PDF 물리)·
# printed_range(책, null=front matter), physical_range([1,page_count]),
# printed_range_available(이 파일에 실제 있는 책 페이지 [1, page_count-offset]),
# user_choices, next_step_guidance가 담긴다.
# next_step_guidance를 따라 챕터를 PDF·책 페이지 둘 다 표기해 보여주고 반드시
# ① 이대로 진행 ② 직접 입력(반드시 PDF 물리 페이지로) ③ 청크 중 선택을 받아라.
# ※ 발췌본: 목차에 책 전체 챕터가 적혀 있어도, 시작 책페이지가
#   printed_range_available을 넘는 챕터는 이 파일에 없으므로 제외한다(서버의
#   from_toc도 범위 밖 항목을 드롭). 포함된 마지막 챕터 끝은 page_count.
# 인코딩이 깨진(모지바케) PDF면 ok=False로 거부하고
# data.recommendations.text_sample에 깨진 텍스트 일부(≤600자)를 담는다.
# 이를 사용자에게 보여주고 ① 무손실 재추출(qpdf) ② OCR(ocrmypdf)
# ③ 그대로 진행 중 선택을 받아라. ③이면 allow_garbled=True로 재호출.
# [OCR 모드] extraction_mode="ocr"이면 텍스트 품질 거부(no_text_layer/garbled)를
# 모두 우회하고, 첫 scan_size 페이지를 JPEG로 렌더해 data.scan_page_images로 준다.
# 서버는 챕터를 제안하지 않는다: primary_mode="analyze_toc_from_images",
# suggested_chapters=[], 균등 청크는 chunk_fallback(목차를 못 읽을 때만)에 분리.
# 목차·offset은 (깨질 수 있는) 텍스트 대신 LLM이 scan_page_images를 읽어 직접
# 파악하고, next_step_guidance 지시에 따라 from_toc 챕터를 구성해 set_chapters한다.
scan_pdf(work_id: str, scan_size: int = 30, allow_garbled: bool = False) -> dict

# 각 chapter 항목 형식: {"chapter_id", "title", "page_range":[s,e],
#                        "printed_range"?:[s,e], "skip"?: bool}
# page_range=PDF 물리(필수·검증 대상), printed_range=책 페이지(표시용 옵셔널).
# skip=True 인 챕터(찾아보기·색인·판권)는 본문 추출과 sub-agent 디스패치,
# HTML 렌더링에서 모두 제외된다.
# language: "ko"|"en". OCR 모드는 텍스트 언어감지가 불가하니 LLM이 이미지로
# 파악한 언어를 반드시 전달(text 모드는 scan_pdf가 자동 감지하므로 생략 가능).
# OCR 모드는 본문 텍스트를 추출하지 않는다(sub-agent가 page_images를 직접 읽음).
set_chapters(work_id: str, chapters: list[dict], book_info: dict = None, language: str = "") -> dict
# text 모드: data에 text(본문).
# ocr  모드: data에 page_images(챕터 페이지를 렌더한 JPEG 절대경로) — sub-agent가
#            순서대로 읽어 본문을 OCR. (그림 추출/렌더링은 제공하지 않는다.)
get_chapter_content(work_id: str, chapter_id: str) -> dict
get_subagent_prompts(work_id: str) -> dict
save_chapter_result(work_id: str, chapter_id: str, data: dict) -> dict
save_extension_result(work_id: str, chapter_id: str, data: dict) -> dict
search_extension_context(work_id: str, chapter_id: str, query: str) -> dict
get_work_state(work_id: str) -> dict
list_pending_chapters(work_id: str) -> dict
# force=False(기본)면 처리 안 된 챕터가 남아 있을 때 ok=False로 거부하고
# data.{summary_pending, extension_pending}를 돌려준다(조용한 부분 렌더링 방지).
# 일부 챕터가 끝내 실패해 부분 결과라도 만들려면 force=True.
finalize_study(work_id: str, output_format: str = "", keep_work_dir: bool = True, force: bool = False) -> dict
# output_format: "html"(정적 사이트) | "md_tui"(챕터별 폴더 + summary.md + 학습 TUI).
# 기본값 없음 — 임의 지정 금지, 반드시 사용자에게 물어볼 것. 미지정 시 ok=False로 거부.
# 둘 다 동일한 중립 JSON(chapters/, extensions/)에서 렌더되므로, 같은 work_id로
# output_format만 바꿔 두 번 호출하면 두 포맷을 모두 생성할 수 있다.
```

## 응답 형식 (모든 도구 통일)

```python
{
  "ok": bool,                     # 성공 여부
  "error": str | None,            # 사람이 읽을 수 있는 에러 메시지
  "data": dict | None,            # 실제 응답 데이터
  "next_action": str | None       # 다음 단계 권장 (LLM 가이드)
}
```

- 에러는 `raise`하지 말고 `ok=False`로 응답에 명시 (MCP 통신 안정성).
- `next_action`은 메인 LLM이 다음 무엇을 호출해야 할지 자연어로 명시.
  **모든 도구가 채운다** — 특히 챕터 처리 루프(get_chapter_content →
  save_chapter_result → save_extension_result)와 list_pending_chapters까지
  "지금 결과로 뭘 하고 다음에 뭘 호출하라"를 담아, 에이전트가 흐름에서
  이탈(예: chapter_id에 페이지 범위 문자열 사용)하지 않게 한다.
- 에러 메시지도 가이드다 — 예: 잘못된 chapter_id는 유효 id 목록과 "특정 페이지는
  scan_page_images를 직접 열라"는 복구 안내를 함께 돌려준다.

## 메인 LLM 워크플로

```
1. init_work(pdf, output, user_context="...", execution_mode="sequential",
             extraction_mode="text" | "ocr")        # 둘 다 사용자에게 물어 결정
   → work_id
2. scan_pdf(work_id, scan_size=30)
   ├ book_metadata (PDF 자체 메타)
   ├ scanned_text (첫 청크 — text 모드. OCR 모드는 신뢰 안 함)
   ├ scan_page_images (OCR 모드: 첫 N페이지 JPEG — LLM이 직접 읽음)
   ├ language (감지된 본문 언어 — OCR 모드는 LLM이 이미지로 파악)
   ├ toc_candidates (text 모드의 본문 목차 후보)
   └ recommendations (페이지 수 기반 챕터 분리 추천 + offset + next_step_guidance)
3. 메인 LLM이 결정:
   ├ 책 정보 추가 추출 (text: scanned_text / ocr: scan_page_images)
   └ chapters 구조 결정 (recommendations 활용 또는 사용자에게 묻기)
      OCR 모드면 scan_page_images에서 목차·offset을 읽어 from_toc 직접 구성
4. set_chapters(work_id, chapters, book_info, language=...)  # ocr이면 language 필수
5. get_subagent_prompts(work_id)
   → {summarizer_prompt, extension_prompt, workflow_instructions, mode}
6. Sub-agent 디스패치 (workflow_instructions에 따라):
   ├ sequential 모드: 한 챕터씩 → save_chapter_result → 다음 챕터
   └ parallel 모드: 최대 5개 동시 디스패치, 결과 도착 순 save 호출
7. get_work_state → 실패 챕터 1회 재시도
8. finalize_study(work_id, output_format=...) → study_output/ 완성
   └ output_format은 기본값 없음 — 사용자에게 html/md_tui 물어 전달.
   └ pending 챕터가 남아 있으면 ok=False로 거부됨. 다 끝낸 뒤 호출하거나,
     부분 결과라도 만들려면 force=True.
   └ next_action의 launch_command(서버 인터프리터 기준)로 학습 자료 실행 안내.
9. 사용자에게 .work/ 보존 여부 묻기 (기본은 보존; 삭제 원하면 keep_work_dir=False)

* 서버 재시작 등으로 work_id가 무효해졌으면(unknown work_id) →
  resume_work(output_dir 또는 pdf_path)로 복원 후 6~8을 이어서 진행.
```

자세한 내용:
- 챕터 분리 모드 및 PDF 처리: [03-pdf-processing.md](./03-pdf-processing.md)
- Sub-agent 프롬프트 및 4유형 문제: [04-content-generation.md](./04-content-generation.md)
- 동시성 보장 (parallel 모드): [06-concurrency.md](./06-concurrency.md)
