# 02. MCP API

## MCP 도구 (12개)

### 도구 시그니처

```python
# output_dir을 비우면 <cwd>/result/<pdf_basename>/ 가 자동 사용된다.
# PDF 이름에서 안전하지 않은 문자는 _ 로 치환된다. 같은 PDF로 재실행하면
# 같은 폴더에 덮어씌워진다.
# 처리 모드(순차/병렬·text/ocr)는 받지 않는다 — 목차 확정 후 set_chapters에서 정한다.
init_work(
    pdf_path: str,
    output_dir: str = "",
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
# 응답 data: {work_id, output_dir, current_phase, execution_mode, extraction_mode,
#             summary_pending, extension_pending}  (모드는 set_chapters 전이면 null)
resume_work(output_dir: str = "", pdf_path: str = "") -> dict

# 챕터 경계 소스는 텍스트 레이어를 신뢰하지 않고 둘 중 하나로만 정한다:
#  (1) 내장 목차(doc.get_toc): primary_mode="from_outline", suggested_chapters에
#      물리 page_range로 담겨 옴. 사용자 확인 후 맞으면 그대로 set_chapters,
#      틀리면 scan_pdf(force_vision=True)로 재호출(아래 (2)로 전환).
#  (2) 내장 목차 없음/force_vision: primary_mode="analyze_toc_from_images",
#      suggested_chapters=[], data.toc_page_images(목차 페이지 JPEG)를 vision으로
#      직독해 from_toc를 직접 구성. 텍스트/스크립트로 목차 추정 금지.
#      균등 청크는 chunk_fallback(목차를 못 읽을 때만)에 분리.
# data.recommendations: page_offset(물리=책+offset), offset_confidence(high/low/none),
#   각 챕터 page_range(PDF 물리)·printed_range(책, null=front matter),
#   physical_range([1,page_count]), printed_range_available([1,page_count-offset]),
#   user_choices, next_step_guidance. scanned_text는 노출하지 않는다(텍스트 불신).
# next_step_guidance를 따라 챕터를 PDF·책 페이지 둘 다 표기해 보여주고, MCP가 준
# user_choices를 그대로 제시(임의 합성/생략 금지)해 선택을 받아라:
#   from_outline → ① 이대로 ② 틀림→vision재분석(force_vision) ③ 직접입력 ④ 청크
#   vision        → ① 이대로 ② 직접입력(PDF 물리 페이지) ③ 청크
# ※ 발췌본: 시작 책페이지가 printed_range_available을 넘는 챕터는 제외(서버도 드롭).
scan_pdf(work_id: str, scan_size: int = 30, force_vision: bool = False) -> dict

# 각 chapter 항목 형식: {"chapter_id", "title", "page_range":[s,e],
#                        "printed_range"?:[s,e], "skip"?: bool}
# page_range=PDF 물리(필수·검증 대상), printed_range=책 페이지(표시용 옵셔널).
# skip=True 인 챕터(찾아보기·색인·판권)는 본문 추출과 sub-agent 디스패치,
# HTML 렌더링에서 모두 제외된다.
# execution_mode("sequential"|"parallel")·extraction_mode("text"|"ocr"): 기본값 없음.
#   임의 지정 금지, 반드시 사용자에게 물어볼 것. 하나라도 미지정/오타면 ok=False로
#   거부하며 data.choices에 4조합+특징을 돌려준다(4개 모두 유효하니 빼지 말고 전부 제시).
#   ※ 목차 분석은 모드와 무관 — 여기서 정하는 건 본문 추출/디스패치 방식뿐.
# language: "ko"|"en". OCR(extraction_mode="ocr")은 텍스트 언어감지가 불가하니 LLM이
#   이미지로 파악한 언어를 반드시 전달(text 모드는 scan_pdf가 자동 감지하므로 생략 가능).
# extraction_mode="ocr"은 본문 텍스트를 추출하지 않는다(sub-agent가 page_images를 직접 읽음).
set_chapters(work_id: str, chapters: list[dict], execution_mode: str = "",
             extraction_mode: str = "", book_info: dict = None, language: str = "") -> dict
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
# 둘 다 동일한 중립 JSON(chapters/{summaries,quiz,extension_questions})에서 렌더되므로, 같은 work_id로
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
  toc_page_images를 직접 열라"는 복구 안내를 함께 돌려준다.

## 메인 LLM 워크플로

```
1. init_work(pdf, output, user_context="...")        # 처리 모드는 받지 않음
   → work_id
2. scan_pdf(work_id, scan_size=30)
   ├ book_metadata (PDF 자체 메타)
   ├ outline_present (내장 목차 유무)
   ├ toc_page_images (내장 목차 없을 때: 목차 페이지 JPEG — LLM이 직접 읽음)
   ├ language (감지된 본문 언어 — vision 경로는 LLM이 이미지로 파악)
   └ recommendations (from_outline | analyze_toc_from_images + offset + next_step_guidance)
   ※ scanned_text는 주지 않는다(텍스트 레이어 불신). 목차는 내장 목차/이미지로만.
3. 메인 LLM이 결정:
   ├ from_outline: suggested_chapters를 사용자에게 보여 확인. 틀리면
   │   scan_pdf(force_vision=True)로 재호출해 toc_page_images를 vision으로 재분석.
   └ analyze_toc_from_images: toc_page_images에서 목차·offset을 읽어 from_toc 직접 구성
      (텍스트/스크립트로 추정 금지)
4. set_chapters(work_id, chapters, execution_mode, extraction_mode, book_info, language=...)
   # execution/extraction 둘 다 사용자에게 물어 결정(미지정 시 4조합 choices로 거부).
   # ocr이면 language 필수.
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
