# 02. MCP API

## MCP 도구 (11개)

### 도구 시그니처

```python
# output_dir을 비우면 <cwd>/result/<pdf_basename>/ 가 자동 사용된다.
# PDF 이름에서 안전하지 않은 문자는 _ 로 치환된다. 같은 PDF로 재실행하면
# 같은 폴더에 덮어씌워진다.
init_work(
    pdf_path: str,
    output_dir: str = "",
    execution_mode: str = "sequential",  # "sequential" (기본) | "parallel"
    enable_multiple_choice: bool = True,
    enable_short_answer: bool = True,
    enable_reflection: bool = True,
    enable_extension: bool = True,
    user_context: str = ""               # 학습자 정보 (선택)
) -> dict
# 응답 data: {work_id, work_dir, output_dir(실제 사용된 절대 경로)}

scan_pdf(work_id: str, scan_size: int = 20) -> dict

# 각 chapter 항목 형식: {"chapter_id", "title", "page_range":[s,e], "skip"?: bool}
# skip=True 인 챕터(찾아보기·색인·판권)는 본문 추출과 sub-agent 디스패치,
# HTML 렌더링에서 모두 제외된다.
set_chapters(work_id: str, chapters: list[dict], book_info: dict = None) -> dict
get_chapter_content(work_id: str, chapter_id: str) -> dict
get_subagent_prompts(work_id: str) -> dict
save_chapter_result(work_id: str, chapter_id: str, data: dict) -> dict
save_extension_result(work_id: str, chapter_id: str, data: dict) -> dict
search_extension_context(work_id: str, chapter_id: str, query: str) -> dict
get_work_state(work_id: str) -> dict
list_pending_chapters(work_id: str) -> dict
finalize_study(work_id: str, output_format: str = "html", keep_work_dir: bool = True) -> dict
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

## 메인 LLM 워크플로

```
1. init_work(pdf, output, user_context="...", execution_mode="sequential")
   → work_id
2. scan_pdf(work_id, scan_size=20)
   ├ book_metadata (PDF 자체 메타)
   ├ scanned_text (첫 청크, 서문 등이 들어있을 가능성)
   ├ language (감지된 본문 언어)
   ├ toc_candidates (본문 목차 후보)
   └ recommendations (페이지 수 기반 챕터 분리 추천)
3. 메인 LLM이 결정:
   ├ scanned_text에서 책 정보 추가 추출 (서문 요약, 출판사 등)
   └ chapters 구조 결정 (recommendations 활용 또는 사용자에게 묻기)
4. set_chapters(work_id, chapters, book_info)
5. get_subagent_prompts(work_id)
   → {summarizer_prompt, extension_prompt, workflow_instructions, mode}
6. Sub-agent 디스패치 (workflow_instructions에 따라):
   ├ sequential 모드: 한 챕터씩 → save_chapter_result → 다음 챕터
   └ parallel 모드: 최대 5개 동시 디스패치, 결과 도착 순 save 호출
7. get_work_state → 실패 챕터 1회 재시도
8. finalize_study(work_id, output_format="html") → study_output/ 완성
9. 사용자에게 .work/ 보존 여부 묻기 (기본은 보존)
```

자세한 내용:
- 챕터 분리 모드 및 PDF 처리: [03-pdf-processing.md](./03-pdf-processing.md)
- Sub-agent 프롬프트 및 4유형 문제: [04-content-generation.md](./04-content-generation.md)
- 동시성 보장 (parallel 모드): [06-concurrency.md](./06-concurrency.md)
