"""Sub-agent 시스템 프롬프트 + 워크플로 지시문 생성."""
from __future__ import annotations

from typing import Any

from . import workspace


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

비활성화된 유형은 0개로 두세요 (재분배 없음).
""".strip()


# ---------------------------------------------------------------------------
# 요약 작성 형식 (마크다운)
#   요약은 렌더 시 마크다운으로 해석된다(HTML은 markdown-it, TUI는 rich).
#   그림(figure)은 다루지 않는다 — 요약은 순수 텍스트/마크다운만.
# ---------------------------------------------------------------------------
SUMMARY_FORMAT = """\
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


# ---------------------------------------------------------------------------
# 기본 문제 작성 기준
#   기본 문제는 현재 챕터 본문으로 검증 가능한 학습 확인용 문제다.
# ---------------------------------------------------------------------------
BASIC_QUESTION_GUIDELINES = """\
[기본 문제 작성 기준]
- 모든 객관식/단답형/주관식 문제는 현재 챕터 본문만으로 정답, 해설,
  model_answer를 유추하고 검증할 수 있어야 합니다.
- 외부 지식, 일반 상식, 다른 챕터 내용, 보이지 않는 이미지 정보를 알아야
  답할 수 있는 문제는 만들지 마세요.
- PDF에 포함된 그림, 도표, 이미지의 시각 정보에 의존하는 문제는 만들지 마세요.
  최종 학습 자료에는 이미지가 포함되지 않습니다.
- 본문 text에 그림 설명이나 캡션이 들어 있더라도, 그 텍스트만으로 충분히
  답할 수 있을 때만 문제로 만드세요.
- reflection도 기본 문제입니다. 개인 의견 토론이 아니라 본문 근거를 바탕으로
  답할 수 있는 검증형 주관식으로 만드세요.
- 학습자 컨텍스트는 난이도, 용어 수준, 예시의 친숙도, 문제 관점을 조정하는 데
  사용하되, 위의 본문 근거 제한보다 우선하지 않습니다.""".strip()


# ---------------------------------------------------------------------------
# 확장 문제 작성 기준
#   확장 문제는 PDF 개념에서 출발해 현실 맥락으로 사고를 넓힌다.
# ---------------------------------------------------------------------------
EXTENSION_GUIDELINES = """\
[확장 문제 작성 기준]
- 단순 회상이나 정의 암기 문제가 아니라, PDF 챕터의 핵심 개념을 현실 맥락,
  실무 적용, 경험 기반 판단 상황, 사회적·기술적 이슈와 연결하는 응용 문제를
  만드세요.
- 꼭 하나의 정답으로 닫히지 않아도 됩니다. 다만 model_answer는 반드시 포함하고,
  좋은 답안의 방향, 핵심 근거, 균형 잡힌 관점, 한계나 반론을 담으세요.
- 현실 사례나 가상 상황을 쓰더라도 PDF 챕터와의 연결이 분명해야 합니다.
- 학습자 컨텍스트를 반영해 난이도와 현실 맥락을 고르세요. 초심자에게는 생활
  예시를, 실무자에게는 운영·설계·의사결정 관점을 더 사용할 수 있습니다.
- 외부 검색이나 외부 자료 수집 도구를 사용하지 마세요. 함께 전달받은 챕터 본문과
  학습자 컨텍스트만으로 문제를 만드세요.
- 최신 사실이나 별도 출처를 알아야만 답할 수 있는 문제 대신, 필요한 상황과 조건을
  question 안에 충분히 제시해 스스로 완결된 문제를 만드세요.""".strip()


# ---------------------------------------------------------------------------
# 입력 방식 블록 (text vs ocr) — extraction_mode에 따라 본문을 어떻게 얻는지
# ---------------------------------------------------------------------------
INPUT_MODE_TEXT = """\
[입력 방식 — 본문 텍스트]
get_chapter_content가 제공한 text가 챕터 본문입니다. 깨진 글자·띄어쓰기 오류·
잘못 분리된 줄이 있으면 의미를 해치지 않는 선에서 자연스럽게 교정해 읽으세요
(임의 추가 금지).""".strip()

INPUT_MODE_OCR = """\
[입력 방식 — OCR 선계산 본문 텍스트]
get_chapter_content가 제공한 text가 PaddleOCR CPU로 미리 읽은 챕터 본문입니다.
깨진 글자·띄어쓰기 오류·잘못 분리된 줄이 있으면 의미를 해치지 않는 선에서
자연스럽게 교정해 읽으세요(임의 추가 금지).""".strip()


# ---------------------------------------------------------------------------
# Summarizer 템플릿
# ---------------------------------------------------------------------------

_SUMMARIZER = """\
당신은 PDF 학습 자료를 만드는 어시스턴트입니다.
원문 언어와 무관하게 주어진 챕터 본문을 읽고 한국어로 ① 요약 ② 핵심 포인트 ③ 검증 문제를 생성하세요.

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

{question_guidelines_block}

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


# ---------------------------------------------------------------------------
# Extension agent 템플릿
# ---------------------------------------------------------------------------

_EXTENSION = """\
당신은 챕터 학습을 한 단계 확장하는 어시스턴트입니다.
함께 전달받은 챕터 본문과 학습자 컨텍스트를 바탕으로 챕터와 연결되는
응용/심화 문제를 만듭니다. 외부 검색이나 별도 자료 수집은 하지 않습니다.

[책 정보]
{book_info_block}

[학습자 컨텍스트]
{user_context_block}

[활성화 — extension만 처리]
객관식/단답/주관식 문제는 만들지 마세요. 그건 다른 sub-agent의 책임입니다.

{extension_guidelines_block}

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
        "model_answer": "..."
      }}
    ]
  }}
}}
```
JSON 본문만 출력. 코드펜스 금지. save_extension_result로 저장.
"""


# ---------------------------------------------------------------------------
# Workflow instructions
# ---------------------------------------------------------------------------

WORKFLOW_INSTRUCTIONS_SEQUENTIAL = """\
한 챕터씩 처리하세요.
1) chapter_ids에서 다음 chapter_id를 고르고, summary_pending_chapter_ids와
   extension_pending_chapter_ids 양쪽의 포함 여부를 확인
2) get_chapter_content(work_id, chapter_id)로 본문을 한 번만 받기
3) summary_pending_chapter_ids에 있으면 summarizer_prompt로 생성한 결과를
   save_chapter_result(work_id, chapter_id, data)로 저장
4) extension_pending_chapter_ids에 있으면 같은 본문과 extension_prompt로 생성한 결과를
   save_extension_result(work_id, chapter_id, data)로 저장
5) 두 목록 중 실제로 포함된 요청된 결과 유형만 저장하고 다음 챕터로 진행
실패 시 1회 재시도. 그래도 실패하면 다음 챕터로.
chapter_ids는 두 pending 목록의 자연 정렬된 합집합입니다.
"""

WORKFLOW_INSTRUCTIONS_PARALLEL = """\
최대 5개 챕터를 동시에 sub-agent로 디스패치하세요.
- 각 sub-agent는 chapter_id가 summary_pending_chapter_ids와
  extension_pending_chapter_ids에 각각 포함되는지 먼저 확인합니다.
- get_chapter_content는 챕터당 한 번만 호출해 본문을 공유합니다.
- summary_pending_chapter_ids에 있으면 summarizer_prompt 결과만
  save_chapter_result로 저장합니다.
- extension_pending_chapter_ids에 있으면 extension_prompt 결과만
  save_extension_result로 저장합니다. 외부 검색은 사용하지 않습니다.
- 두 목록 중 실제로 포함된 요청된 결과 유형만 저장하세요.
- save_*는 서버가 동시성을 보장하므로 결과 도착 순서대로 호출 가능합니다.
- 5개 배치 완료 후 다음 5개 시작.
- 실패 챕터는 모든 배치 종료 후 1회 재시도.
chapter_ids는 두 pending 목록의 자연 정렬된 합집합입니다.
"""


# ---------------------------------------------------------------------------
# build_prompts
# ---------------------------------------------------------------------------

def _format_book_info_block(book_info: dict[str, Any] | None) -> str:
    if not book_info:
        return "(미확인)"
    parts = []
    title = book_info.get("title")
    author = book_info.get("author")
    publisher = book_info.get("publisher")
    year = book_info.get("publication_year")
    preface = book_info.get("preface_summary")
    if title:
        parts.append(f"- 제목: {title}")
    if author:
        parts.append(f"- 저자: {author}")
    if publisher:
        parts.append(f"- 출판사: {publisher}")
    if year:
        parts.append(f"- 출판년도: {year}")
    if preface:
        parts.append(f"- 책 소개: {preface}")
    return "\n".join(parts) if parts else "(미확인)"


def _format_enabled_types(opts: dict[str, bool]) -> str:
    labels = {
        "multiple_choice": "객관식 (mc)",
        "short_answer": "단답형 (sa)",
        "reflection": "주관식 (rf)",
        "extension": "확장형 (ex) — extension agent가 별도 처리",
    }
    lines = []
    for key, label in labels.items():
        mark = "✓" if opts.get(key) else "✗"
        lines.append(f"- {mark} {label}")
    return "\n".join(lines)


def _format_user_context(uc: str) -> str:
    if not uc:
        return "(제공되지 않음)"
    return (
        uc
        + "\n위 컨텍스트를 고려해 난이도, 표현 수준, 예시, 문제 관점을 맞추세요. "
        "단, PDF 본문 근거 제한을 약화하지 마세요."
    )


def build_prompts(state: dict[str, Any], book_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """state + book_info를 토대로 sub-agent 프롬프트 묶음 생성.

    Returns:
        {
            "mode": "sequential" | "parallel",
            "summarizer_prompt": str,
            "extension_prompt": str | None,   # extension 비활성이면 None
            "workflow_instructions": str,
            "chapter_ids": [str, ...],
            "summary_pending_chapter_ids": [str, ...],
            "extension_pending_chapter_ids": [str, ...],
            "enabled_types": {"multiple_choice": bool, ...},
        }
    """
    opts = state.get("question_options", {})
    user_context = state.get("user_context", "") or ""
    mode = state.get("execution_mode", "sequential")
    ocr_mode = state.get("extraction_mode", "text") == "ocr"

    book_info_block = _format_book_info_block(book_info)
    user_context_block = _format_user_context(user_context)
    enabled_types_block = _format_enabled_types(opts)
    input_mode_block = INPUT_MODE_OCR if ocr_mode else INPUT_MODE_TEXT
    summarizer_prompt = _SUMMARIZER.format(
        book_info_block=book_info_block,
        user_context_block=user_context_block,
        enabled_types_block=enabled_types_block,
        scales_table=QUESTION_SCALES_TABLE,
        input_mode_block=input_mode_block,
        summary_format_block=SUMMARY_FORMAT,
        question_guidelines_block=BASIC_QUESTION_GUIDELINES,
    )

    extension_prompt: str | None = None
    if opts.get("extension"):
        extension_prompt = _EXTENSION.format(
            book_info_block=book_info_block,
            user_context_block=user_context_block,
            scales_table=QUESTION_SCALES_TABLE,
            extension_guidelines_block=EXTENSION_GUIDELINES,
        )

    workflow = (
        WORKFLOW_INSTRUCTIONS_PARALLEL
        if mode == "parallel"
        else WORKFLOW_INSTRUCTIONS_SEQUENTIAL
    )
    if ocr_mode:
        ocr_note = (
            "[OCR 모드] get_chapter_content는 set_chapters에서 PaddleOCR CPU로 "
            "선계산한 본문 text를 돌려줍니다. 각 챕터의 text를 읽고 요약/문제를 "
            "생성하세요.\n\n"
        )
        workflow = ocr_note + workflow

    # 결과 유형별 pending 목록의 합집합만 sub-agent 디스패치 대상으로 노출
    all_chapter_ids = sorted(state.get("chapters", {}).keys(), key=_chapter_sort_key)
    pending = workspace.pending_chapters_from_state(state)
    summary_pending = pending["summary_pending"]
    extension_pending = pending["extension_pending"]
    pending_ids = set(summary_pending) | set(extension_pending)
    chapter_ids = [cid for cid in all_chapter_ids if cid in pending_ids]
    skipped_chapter_ids = [
        cid for cid in all_chapter_ids if state["chapters"][cid].get("skip")
    ]

    return {
        "mode": mode,
        "extraction_mode": "ocr" if ocr_mode else "text",
        "summarizer_prompt": summarizer_prompt,
        "extension_prompt": extension_prompt,
        "workflow_instructions": workflow,
        "chapter_ids": chapter_ids,
        "summary_pending_chapter_ids": summary_pending,
        "extension_pending_chapter_ids": extension_pending,
        "skipped_chapter_ids": skipped_chapter_ids,
        "enabled_types": {k: bool(opts.get(k)) for k in
                          ("multiple_choice", "short_answer", "reflection", "extension")},
    }


def _chapter_sort_key(cid: str) -> tuple[int, str]:
    """ch1, ch2, ch10이 자연스럽게 정렬되도록."""
    if cid.startswith("ch") and cid[2:].isdigit():
        return (int(cid[2:]), cid)
    return (10**9, cid)
