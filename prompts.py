"""Sub-agent 시스템 프롬프트 + 워크플로 지시문 생성.

build_prompts(state)가 state.json을 보고 user_context, language,
question_options, execution_mode를 모두 주입한 최종 프롬프트 dict를 반환.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# 챕터 글자 수 기준 동적 상한 (docs/04)
#   합계는 옵션 비활성 유형을 0으로 둔다 (재분배 없음).
# ---------------------------------------------------------------------------
QUESTION_SCALES_TABLE = """
| 챕터 글자 수 | 객관식(mc) | 단답형(sa) | 주관식(rf) | 확장형(ex) |
|---|---|---|---|---|
| < 3,000        | 3 | 1 | 1 | 1 |
| 3,000–10,000   | 5 | 2 | 2 | 1 |
| 10,000–25,000  | 7 | 3 | 2 | 2 |
| 25,000+        | 10 | 4 | 3 | 3 |

위 표는 생성해야 하는 목표치가 아니라 **최대 개수**입니다. 본문에서 충분히 좋은
문제를 만들 근거가 부족하면 더 적게 생성하세요. 최대 개수를 맞추기 위해 중복되거나
사소하거나 본문 근거가 약한 문제를 억지로 채우지 마세요.

The table above defines **maximum counts**, not targets. Generate fewer
questions when the chapter does not support high-quality, non-duplicative
items; do not pad the output with weak, trivial, repetitive, or poorly grounded
questions just to reach the maximum.

비활성화된 유형은 0개로 두세요 (재분배 없음).
""".strip()


# ---------------------------------------------------------------------------
# 요약 작성 형식 (마크다운)
#   요약은 렌더 시 마크다운으로 해석된다(HTML은 markdown-it, TUI는 rich).
#   그림(figure)은 다루지 않는다 — 요약은 순수 텍스트/마크다운만.
# ---------------------------------------------------------------------------
SUMMARY_FORMAT_KO = """\
[요약 작성 형식 — 마크다운]
summary는 **마크다운**으로 작성하세요. 렌더러가 그대로 마크다운으로 해석합니다
(평문 나열 금지). 다음을 적극 활용하세요:
- `##`/`###` 소제목으로 구획을 나눠 가독성을 높이기
- **굵게**, *기울임*, `인라인 코드`, 코드블록(```), 목록(-, 1.), 표(| … |)
- 정의·수식·예약어는 코드/표로 정리하면 더 또렷해집니다

본문에 `3.1`, `3.2` 같은 번호가 붙은 서브 챕터가 있으면 **각 서브 챕터마다**
독립된 `## 3.1 ...`, `## 3.2 ...` 섹션을 만들어 요약하세요. 서브 챕터 번호가
없으면 본문의 실제 소제목이나 의미 단락을 기준으로 `##`/`###` 섹션을 나누세요.
챕터 전체를 하나의 덩어리 문단으로만 요약하지 마세요.

이미지(그림)는 넣지 마세요 — `![...]()` 같은 이미지 문법은 사용하지 않습니다.
필요한 그림 내용은 글/표로 풀어 설명하세요.""".strip()

SUMMARY_FORMAT_EN = """\
[Summary format — Markdown]
Write `summary` in **Markdown**; the renderer interprets it as Markdown
(HTML via markdown-it, TUI via rich). Do not dump flat prose. Use freely:
- `##`/`###` subheadings to structure the summary for readability
- **bold**, *italics*, `inline code`, code blocks (```), lists (-, 1.), tables (| … |)
- Render definitions, formulas, and keywords as code/tables when it clarifies

If the body contains numbered subchapters such as `3.1` and `3.2`, create
one section per subchapter with headings like `## 3.1 ...` and `## 3.2 ...`.
If there are no numbered subchapters, divide the summary by the source's real
subheadings or meaningful topic breaks. Do not summarize the whole chapter as
one undifferentiated block.

Do not embed images — never use image syntax like `![...]()`. Describe any
needed figure content in prose or tables instead.""".strip()


# ---------------------------------------------------------------------------
# 입력 방식 블록 (text vs ocr) — extraction_mode에 따라 본문을 어떻게 얻는지
# ---------------------------------------------------------------------------
INPUT_MODE_TEXT_KO = """\
[입력 방식 — 본문 텍스트]
get_chapter_content가 제공한 text가 챕터 본문입니다. 깨진 글자·띄어쓰기 오류·
잘못 분리된 줄이 있으면 의미를 해치지 않는 선에서 자연스럽게 교정해 읽으세요
(임의 추가 금지).""".strip()

INPUT_MODE_OCR_KO = """\
[입력 방식 — 페이지 이미지(OCR)]
본문 텍스트는 제공되지 않습니다. get_chapter_content가 주는 page_images
(페이지 JPEG 절대경로)를 순서대로 멀티모달 입력으로 읽어 본문을 직접
파악하세요(=OCR). 흐릿하거나 깨져 보이는 기술용어·식별자·예약어
(예: SERIALIZABLE, KEY_BLOCK_SIZE, select_type)는 문맥으로 복원하세요.
읽어낸 본문의 글자수를 헤아려 위의 문제 개수 표를 적용하세요.
**또한 이미지에서 읽어낸 본문 전체를 출력 JSON의 `body_text` 필드에 그대로
담으세요**(요약이 아니라 전사한 원문 — 페이지 순서대로 이어붙임). 서버가 이를
raw_data에 보존합니다.""".strip()

INPUT_MODE_TEXT_EN = """\
[Input mode — body text]
The `text` from get_chapter_content is the chapter body. If it has broken
characters, spacing errors, or split lines, read with natural corrections where
meaning is preserved (do not invent content).""".strip()

INPUT_MODE_OCR_EN = """\
[Input mode — page images (OCR)]
No body text is provided. Read the `page_images` (absolute JPEG paths) from
get_chapter_content in order, as multimodal input, to recover the body yourself
(=OCR). Reconstruct blurry/garbled technical terms, identifiers, and keywords
(e.g. SERIALIZABLE, KEY_BLOCK_SIZE, select_type) from context. Count the chars
you read and apply the question counts table above.
**Also put the full transcribed body (not a summary — the verbatim text you read,
concatenated in page order) into the `body_text` field of the output JSON.** The
server preserves it in raw_data.""".strip()


# ---------------------------------------------------------------------------
# Summarizer 템플릿
# ---------------------------------------------------------------------------

_SUMMARIZER_KO = """\
당신은 PDF 학습 자료를 만드는 어시스턴트입니다.
주어진 챕터 본문을 읽고 ① 요약 ② 핵심 포인트 ③ 검증 문제를 생성하세요.

[책 정보]
{book_info_block}

[학습자 컨텍스트]
{user_context_block}

[활성화된 문제 유형]
{enabled_types_block}

[챕터 글자 수별 최대 문제 개수]
{scales_table}

{input_mode_block}

{summary_format_block}

[출력 형식 — JSON]
반드시 다음 스키마의 **JSON 객체 하나만** 반환하세요. 객체 전체를 감싸는
코드펜스(```)는 금지하지만, summary 값 **안에서는** 마크다운(코드블록 포함)을
자유롭게 쓰세요. summary의 줄바꿈은 **실제 줄바꿈(개행)**으로 넣으세요 —
`\\n` 같은 글자를 직접 타이핑하지 마세요(JSON 직렬화는 도구가 알아서 합니다).

{{
  "chapter_id": "<주어진 chapter_id 그대로>",
  "title": "<주어진 title 그대로>",
  "summary": "<한국어 요약 (마크다운, 서브 챕터가 있으면 서브 챕터별 섹션)>",
  "key_points": ["...", "..."],
  "body_text": "<OCR 모드에서만: 페이지 이미지에서 전사한 본문 전체. text 모드는 생략>",
  "questions": {{
    "multiple_choice": [
      {{
        "id": "mc_1",
        "question": "...",
        "options": ["A", "B", "C", "D"],
        "answer_index": 0,
        "explanation": "..."
      }}
    ],
    "short_answer": [
      {{"id": "sa_1", "question": "...", "model_answer": "..."}}
    ],
    "reflection": [
      {{"id": "rf_1", "question": "...", "model_answer": "..."}}
    ]
  }}
}}

비활성화된 유형은 해당 키를 빈 배열([])로 두세요. 키 자체는 유지.
저장은 save_chapter_result(work_id, chapter_id, <이 JSON>)로 보냅니다.
"""

_SUMMARIZER_EN = """\
You are an assistant that builds study material from PDFs.
Read the given chapter and produce ① summary, ② key points, ③ verification questions.

[Book info]
{book_info_block}

[Learner context]
{user_context_block}

[Enabled question types]
{enabled_types_block}

[Maximum question counts per chapter size]
{scales_table}

{input_mode_block}

{summary_format_block}

[Output format — JSON]
Return **exactly one JSON object** matching the schema below. Do not wrap the
whole object in a code fence, but **inside** the summary value use Markdown
freely (including code blocks). Put **real line breaks** in summary — do NOT
type literal `\\n` characters (the tool handles JSON serialization).

{{
  "chapter_id": "<as given>",
  "title": "<as given>",
  "summary": "<English summary (Markdown, sectioned by subchapter when present)>",
  "key_points": ["...", "..."],
  "body_text": "<OCR mode only: full verbatim body transcribed from page images. Omit in text mode>",
  "questions": {{
    "multiple_choice": [
      {{"id": "mc_1", "question": "...", "options": ["A","B","C","D"], "answer_index": 0, "explanation": "..."}}
    ],
    "short_answer": [
      {{"id": "sa_1", "question": "...", "model_answer": "..."}}
    ],
    "reflection": [
      {{"id": "rf_1", "question": "...", "model_answer": "..."}}
    ]
  }}
}}

Disabled types must be present with an empty array []. Keys stay.
Send via save_chapter_result(work_id, chapter_id, <this JSON>).
"""


# ---------------------------------------------------------------------------
# Extension agent 템플릿
# ---------------------------------------------------------------------------

_EXTENSION_KO = """\
당신은 챕터 학습을 한 단계 확장하는 어시스턴트입니다.
search_extension_context(work_id, chapter_id, query)를 호출해 외부 자료를
수집한 뒤, 챕터와 연결되는 응용/심화 문제를 만듭니다.

[책 정보]
{book_info_block}

[학습자 컨텍스트]
{user_context_block}

[활성화 — extension만 처리]
객관식/단답/주관식 문제는 만들지 마세요. 그건 다른 sub-agent의 책임입니다.

[검색]
- 챕터 본문에서 흥미로운 키워드를 1–3개 골라 query를 만듭니다.
- search_extension_context 결과의 출처 URL을 그대로 sources에 기재.
- 결과가 비거나 실패하면 본문 지식만으로 만들고 sources는 빈 배열.

[최대 개수]
{scales_table}

[출력 형식 — JSON]
```text
{{
  "chapter_id": "<as given>",
  "questions": {{
    "extension": [
      {{
        "id": "ex_1",
        "question": "...",
        "context": "외부 자료에서 가져온 200–400자 요약",
        "model_answer": "...",
        "sources": ["https://...", "..."]
      }}
    ]
  }}
}}
```
JSON 본문만 출력. 코드펜스 금지. save_extension_result로 저장.
"""

_EXTENSION_EN = """\
You extend a chapter's study material with applied/deeper questions.
Call search_extension_context(work_id, chapter_id, query) to gather external
sources, then craft extension-type questions tied to the chapter.

[Book info]
{book_info_block}

[Learner context]
{user_context_block}

[Scope — extension only]
Do NOT produce multiple_choice / short_answer / reflection. Another sub-agent handles those.

[Search]
- Pick 1–3 keywords from the chapter for queries.
- Copy source URLs into `sources` verbatim.
- If results are empty or fail, rely on chapter knowledge alone and leave sources as [].

[Maximum counts]
{scales_table}

[Output format — JSON]
```text
{{
  "chapter_id": "<as given>",
  "questions": {{
    "extension": [
      {{
        "id": "ex_1",
        "question": "...",
        "context": "200–400 char summary from external sources",
        "model_answer": "...",
        "sources": ["https://...", "..."]
      }}
    ]
  }}
}}
```
JSON only. No code fences. Save via save_extension_result.
"""


# ---------------------------------------------------------------------------
# Workflow instructions
# ---------------------------------------------------------------------------

WORKFLOW_INSTRUCTIONS_SEQUENTIAL = """\
한 챕터씩 처리하세요.
1) get_chapter_content(work_id, chapter_id) — 본문 받기
2) 위 summarizer 시스템 프롬프트로 sub-agent 호출 (없으면 본인이 직접 처리)
3) 결과 JSON을 save_chapter_result(work_id, chapter_id, data)
4) extension이 활성화돼 있으면 동일한 방식으로 extension sub-agent 호출 → save_extension_result
5) 다음 챕터로 진행
실패 시 1회 재시도. 그래도 실패하면 다음 챕터로.
chapter_ids는 get_subagent_prompts 응답에 포함됩니다.
"""

WORKFLOW_INSTRUCTIONS_PARALLEL = """\
최대 5개 챕터를 동시에 sub-agent로 디스패치하세요.
- 각 sub-agent는 get_chapter_content → 처리 → save_chapter_result까지 완수.
- save_*는 서버가 동시성을 보장하므로 결과 도착 순서대로 호출 가능합니다.
- 5개 배치 완료 후 다음 5개 시작.
- extension도 동일하게 병렬 처리 가능.
- 실패 챕터는 모든 배치 종료 후 1회 재시도.
chapter_ids는 get_subagent_prompts 응답에 포함됩니다.
"""


# ---------------------------------------------------------------------------
# build_prompts
# ---------------------------------------------------------------------------

def _format_book_info_block(book_info: dict[str, Any] | None, lang: str) -> str:
    if not book_info:
        return "(미확인)" if lang == "ko" else "(unknown)"
    parts = []
    title = book_info.get("title")
    author = book_info.get("author")
    publisher = book_info.get("publisher")
    year = book_info.get("publication_year")
    preface = book_info.get("preface_summary")
    if title:
        parts.append(f"- 제목: {title}" if lang == "ko" else f"- Title: {title}")
    if author:
        parts.append(f"- 저자: {author}" if lang == "ko" else f"- Author: {author}")
    if publisher:
        parts.append(f"- 출판사: {publisher}" if lang == "ko" else f"- Publisher: {publisher}")
    if year:
        parts.append(f"- 출판년도: {year}" if lang == "ko" else f"- Year: {year}")
    if preface:
        label = "책 소개" if lang == "ko" else "Preface"
        parts.append(f"- {label}: {preface}")
    return "\n".join(parts) if parts else ("(미확인)" if lang == "ko" else "(unknown)")


def _format_enabled_types(opts: dict[str, bool], lang: str) -> str:
    labels_ko = {
        "multiple_choice": "객관식 (mc)",
        "short_answer": "단답형 (sa)",
        "reflection": "주관식 (rf)",
        "extension": "확장형 (ex) — extension agent가 별도 처리",
    }
    labels_en = {
        "multiple_choice": "multiple_choice (mc)",
        "short_answer": "short_answer (sa)",
        "reflection": "reflection (rf)",
        "extension": "extension (ex) — handled by extension agent",
    }
    labels = labels_ko if lang == "ko" else labels_en
    lines = []
    for key, label in labels.items():
        mark = "✓" if opts.get(key) else "✗"
        lines.append(f"- {mark} {label}")
    return "\n".join(lines)


def _format_user_context(uc: str, lang: str) -> str:
    if not uc:
        return "(제공되지 않음)" if lang == "ko" else "(none provided)"
    suffix = (
        "\n위 컨텍스트를 고려해 난이도, 표현 수준, 예시를 맞추세요."
        if lang == "ko"
        else "\nAdjust difficulty, tone, and examples to fit the context above."
    )
    return uc + suffix


def build_prompts(state: dict[str, Any], book_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """state + book_info를 토대로 sub-agent 프롬프트 묶음 생성.

    Returns:
        {
            "mode": "sequential" | "parallel",
            "language": "ko" | "en",
            "summarizer_prompt": str,
            "extension_prompt": str | None,   # extension 비활성이면 None
            "workflow_instructions": str,
            "chapter_ids": [str, ...],
            "enabled_types": {"multiple_choice": bool, ...},
        }
    """
    language = (state.get("language") or "en").lower()
    if language not in ("ko", "en"):
        language = "en"

    opts = state.get("question_options", {})
    user_context = state.get("user_context", "") or ""
    mode = state.get("execution_mode", "sequential")
    ocr_mode = state.get("extraction_mode", "text") == "ocr"

    book_info_block = _format_book_info_block(book_info, language)
    user_context_block = _format_user_context(user_context, language)
    enabled_types_block = _format_enabled_types(opts, language)

    if language == "ko":
        input_mode_block = INPUT_MODE_OCR_KO if ocr_mode else INPUT_MODE_TEXT_KO
        summary_format_block = SUMMARY_FORMAT_KO
    else:
        input_mode_block = INPUT_MODE_OCR_EN if ocr_mode else INPUT_MODE_TEXT_EN
        summary_format_block = SUMMARY_FORMAT_EN

    summ_tmpl = _SUMMARIZER_KO if language == "ko" else _SUMMARIZER_EN
    summarizer_prompt = summ_tmpl.format(
        book_info_block=book_info_block,
        user_context_block=user_context_block,
        enabled_types_block=enabled_types_block,
        scales_table=QUESTION_SCALES_TABLE,
        input_mode_block=input_mode_block,
        summary_format_block=summary_format_block,
    )

    extension_prompt: str | None = None
    if opts.get("extension"):
        ext_tmpl = _EXTENSION_KO if language == "ko" else _EXTENSION_EN
        extension_prompt = ext_tmpl.format(
            book_info_block=book_info_block,
            user_context_block=user_context_block,
            scales_table=QUESTION_SCALES_TABLE,
        )

    workflow = (
        WORKFLOW_INSTRUCTIONS_PARALLEL
        if mode == "parallel"
        else WORKFLOW_INSTRUCTIONS_SEQUENTIAL
    )
    if ocr_mode:
        ocr_note = (
            "[OCR 모드] get_chapter_content는 본문 text 대신 page_images(페이지 "
            "이미지 절대경로)를 돌려줍니다. 각 챕터에서 page_images를 순서대로 "
            "멀티모달로 읽어 본문을 파악한 뒤 요약/문제를 생성하세요.\n\n"
        )
        workflow = ocr_note + workflow

    # 비본문(skipped) 챕터는 sub-agent 디스패치 대상에서 제외
    all_chapter_ids = sorted(state.get("chapters", {}).keys(), key=_chapter_sort_key)
    chapter_ids = [
        cid for cid in all_chapter_ids
        if not state["chapters"][cid].get("skip")
    ]
    skipped_chapter_ids = [cid for cid in all_chapter_ids if cid not in chapter_ids]

    return {
        "mode": mode,
        "extraction_mode": "ocr" if ocr_mode else "text",
        "language": language,
        "summarizer_prompt": summarizer_prompt,
        "extension_prompt": extension_prompt,
        "workflow_instructions": workflow,
        "chapter_ids": chapter_ids,
        "skipped_chapter_ids": skipped_chapter_ids,
        "enabled_types": {k: bool(opts.get(k)) for k in
                          ("multiple_choice", "short_answer", "reflection", "extension")},
    }


def _chapter_sort_key(cid: str) -> tuple[int, str]:
    """ch1, ch2, ch10이 자연스럽게 정렬되도록."""
    if cid.startswith("ch") and cid[2:].isdigit():
        return (int(cid[2:]), cid)
    return (10**9, cid)
