"""Sub-agent 시스템 프롬프트 + 워크플로 지시문 생성."""
from __future__ import annotations

import json
from typing import Any

from . import question_contract, summary_contract, workspace


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

위 표는 생성해야 하는 목표치가 아니라 **최대 개수**입니다. 요약에서 충분히 좋은
문제를 만들 근거가 부족하면 더 적게 생성하세요. 최대 개수를 맞추기 위해 중복되거나
사소하거나 요약 근거가 약한 문제를 억지로 채우지 마세요.

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
- **굵게**, *기울임*, `인라인 코드`, 코드블록(```), 목록(-, 1.), 표(| … |)
- 정의·수식·예약어는 코드/표로 정리하면 더 또렷해집니다

section_inventory에 명시적인 서브 챕터가 있으면 **반드시 모든 서브 챕터**를
summary에 포함하세요. `sections` 배열의 순서를 그대로 따라 각 `heading`을 Markdown
제목으로 먼저 쓰고, 해당 section의 요약 내용을 그 제목 아래에 작성하세요.
`level`과 `parent_id`가 나타내는 상대 계층도 Markdown 제목 단계로 보존해야 합니다.
제목을 합치거나, 생략하거나, 순서를 바꾸거나, 다른 제목으로 바꾸지 마세요.
inventory에 명시적인 서브 챕터가 없으면 원문에 없던 서브 챕터나 계층을 만들지
마세요. 가독성이 필요하면 원문 구조처럼 보이는 추가 제목 대신 목록·표·문단을
사용하세요.

이미지(그림)는 넣지 마세요 — `![...]()` 같은 이미지 문법은 사용하지 않습니다.
필요한 그림 내용은 글/표로 풀어 설명하세요.""".strip()


# ---------------------------------------------------------------------------
# 의미 보존 요약 기준
#   구조 inventory는 내용 필터가 아니다. 각 section의 원문 전체를 직접 읽어
#   학습 자료를 만들고 최종 요약을 독립적으로 검토한다.
# ---------------------------------------------------------------------------
SEMANTIC_COMPLETENESS_GUIDELINES = """\
[의미 보존 기준]
이 결과는 짧은 초록이 아니라 원문을 읽은 뒤 복습할 수 있는 **학습 자료**입니다.
특정 글자 수나 압축률을 목표로 삼지 마세요. 원문의 전달 방식과 정보 밀도에 맞춰
필요한 만큼 충분히 설명하세요.

- section_inventory는 원문 구조만 안내하며 요약 범위를 제한하지 않습니다. inventory
  항목만 보고 요약하지 말고 각 section의 원문 전체를 직접 읽으세요.
- 원문에 명시적인 서브 챕터가 있으면 모든 제목과 계층을 summary에 드러내고,
  각 서브 챕터가 독립적으로 복습 가능한 학습 설명을 갖게 하세요.
- 명시적인 서브 챕터가 없으면 챕터 전체의 논리와 흐름을 유지해 요약하세요.
  원문에 없는 서브 챕터 경계를 발명하지 마세요.
- 문서 성격에 따라 핵심 주장, 개념과 정의, 원리와 인과관계, 절차와 선후관계,
  조건과 예외, 비교와 트레이드오프, 근거와 대표 사례, 주의사항 중 실제로 중요한
  요소를 보존하세요.
- 반복, 장식적 문장, 같은 의미의 중복 사례는 줄일 수 있지만 서로 다른 의미,
  전제, 예외, 단계, 판단 근거를 짧게 만들기 위해 삭제하지 마세요.
- 제목이나 용어만 나열하지 말고 학습자가 이해할 수 있도록 관계와 맥락을
  설명하세요.""".strip()


SECTION_INVENTORY_GUIDELINES = """\
[section inventory 작성 기준 — 원문 구조만]
챕터 text 전체를 읽고 요약을 쓰기 전에 원문이 실제로 가진 서브 챕터 구조만
section_inventory로 정리하세요. 이 단계에서는 내용을 요약하지 마세요. 중요 내용을
선정하는 단계도 아닙니다.

- 번호가 붙은 제목, 번호가 없는 제목, 글자 형태가 다른 제목, 깊이가 서로 다른 계층을
  모두 허용합니다. 번호 패턴 하나에 의존하지 말고 제목이 뒤따르는 내용을
  조직하는 실제 구조인지 판단하세요.
- 독립된 제목 줄, 앞뒤 여백, 반복되는 제목 형식, 뒤따르는 본문, 이웃 제목과의
  계층 관계를 함께 보세요. PDF 텍스트 추출 때문에 줄이나 공백이 어색해도 의미상
  같은 제목이면 자연스럽게 복원할 수 있습니다.
- `3장을 참고`, `앞 장에서 설명했듯이`, `2.1에서 살펴본`처럼 문장 안의 단순 언급,
  인용·교차 참조, 목록 번호, 표·그림 번호, 페이지 머리말·꼬리말, 목차 조각의 반복은
  현재 챕터의 서브 챕터로 등록하지 마세요.
- 명시적인 서브 챕터가 하나라도 있으면 `has_explicit_subchapters=true`로 두고 모든
  실제 서브 챕터를 원래 순서대로 기록하세요. `level`은 현재 챕터 안의 상대 깊이를
  1부터 시작하고, `parent_id`로 가장 가까운 상위 section을 연결하세요.
- 명시적인 서브 챕터가 없으면 `has_explicit_subchapters=false`로 두고 `챕터 전체`
  section 하나만 만드세요. 의미 단락을 원문 서브 챕터처럼 발명하지 마세요.
- section id는 영문자·숫자·`_`·`-`만 사용하고 챕터 안에서 중복되지 않게 만드세요.
- 아직 summary, key_points, 문제나 내용 선별 목록을 만들지 마세요.""".strip()


# ---------------------------------------------------------------------------
# 기본 문제 작성 기준
#   기본 문제는 먼저 만든 요약으로 검증 가능한 학습 확인용 문제다.
# ---------------------------------------------------------------------------
QUESTION_SELF_CONTAINEDNESS_GUIDELINES = """\
[문제 문장 독립성]
- 학습자가 요약을 다시 열어 문제의 대상·의도·조건을 보충하지 않아도, `question`
  한 문장만 읽고 무엇을 답하거나 판단해야 하는지 이해할 수 있게 쓰세요. 요약은
  문제 문맥을 보충하는 자료가 아니라 정답·해설·model_answer의 근거입니다.
- 모든 문제에는 묻는 실제 개념·대상·상황을 이름으로 넣고, 답의 범위를 정하는 데
  필요한 조건을 question 안에 함께 제시하세요. 객관식은 보기만 읽어야 주제를
  알 수 있게 만들지 마세요.
- `챕터 핵심 내용으로 옳은 설명은?`, `핵심 원칙을 설명하시오.`, `위 내용에 따르면`,
  `이 챕터에서`, `실무 적용 시 무엇을 우선 점검하겠는가?`처럼 제목·요약·앞선
  문맥을 가리키기만 하는 일반 템플릿은 사용하지 마세요.
- 단답형·주관식·확장형도 특정 개념 또는 구체적 상황을 question에 명시하세요.
  예를 들어 "핵심 원칙" 대신 해당 원칙의 이름과 적용 조건을 물으세요.
- 각 문제를 만든 뒤 summary와 key_points를 숨긴 상태에서도 학습자가 문제의
  대상, 해야 할 판단 또는 답변, 필요한 조건을 알 수 있는지 점검하세요. 하나라도
  빠졌으면 question을 다시 쓰세요.
""".strip()


BASIC_QUESTION_GUIDELINES = """\
[기본 문제 작성 기준 — 요약만 사용]
- 모든 객관식/단답형/주관식 문제는 먼저 확정한 summary와 key_points만으로 정답, 해설,
  model_answer를 유추하고 검증할 수 있어야 합니다.
- 원문 본문을 함께 받았더라도 문제를 만들 때는 다시 참조하지 마세요. summary 또는
  key_points에 명시되지 않은 원문의 세부 사실을 문제·보기·정답·해설에 넣지 마세요.
- 외부 지식, 일반 상식, 다른 챕터 내용, 보이지 않는 이미지 정보를 알아야
  답할 수 있는 문제는 만들지 마세요.
- PDF에 포함된 그림, 도표, 이미지의 시각 정보에 의존하는 문제는 만들지 마세요.
  최종 학습 자료에는 이미지가 포함되지 않습니다.
- 요약에 그림 설명이나 캡션 내용이 들어 있더라도, 요약 텍스트만으로 충분히 답할 수
  있을 때만 문제로 만드세요.
- reflection도 기본 문제입니다. 개인 의견 토론이 아니라 요약 근거를 바탕으로
  요약에서 답할 수 있는 검증형 주관식으로 만드세요.
- 학습자 컨텍스트는 난이도, 용어 수준, 예시의 친숙도, 문제 관점을 조정하는 데
  사용하되, 위의 요약 근거 제한보다 우선하지 않습니다.
- 요약에 좋은 문제를 만들 근거가 부족하면 문제 수를 줄이세요. 원문에서 근거를
  보충해 최대 개수를 채우면 안 됩니다.

{question_self_containedness_block}""".strip()


# ---------------------------------------------------------------------------
# 확장 문제 작성 기준
#   확장 문제는 PDF 개념에서 출발해 현실 맥락으로 사고를 넓힌다.
# ---------------------------------------------------------------------------
EXTENSION_GUIDELINES = """\
[확장 문제 작성 기준 — 요약만 사용]
- 단순 회상이나 정의 암기 문제가 아니라, 챕터 요약의 핵심 개념을 현실 맥락,
  실무 적용, 경험 기반 판단 상황, 사회적·기술적 이슈와 연결하는 응용 문제를
  만드세요.
- 꼭 하나의 정답으로 닫히지 않아도 됩니다. 다만 model_answer는 반드시 포함하고,
  좋은 답안의 방향, 핵심 근거, 균형 잡힌 관점, 한계나 반론을 담으세요.
- 현실 사례나 가상 상황을 쓰더라도 챕터 요약과의 연결이 분명해야 합니다.
- 학습자 컨텍스트를 반영해 난이도와 현실 맥락을 고르세요. 초심자에게는 생활
  예시를, 실무자에게는 운영·설계·의사결정 관점을 더 사용할 수 있습니다.
- 외부 검색이나 외부 자료 수집 도구를 사용하지 마세요. 입력으로 받은 summary,
  key_points와 학습자 컨텍스트만으로 문제를 만드세요.
- 원문 text를 받거나 다시 읽지 마세요. summary 또는 key_points에 없는 원문 세부
  사실을 문제나 model_answer의 근거로 사용하지 마세요.
- 최신 사실이나 별도 출처를 알아야만 답할 수 있는 문제를 만들지 마세요.

{question_self_containedness_block}""".strip()


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

_SECTION_INVENTORY = """\
당신은 PDF 학습 자료의 원문 구조를 정리하는 분석자입니다.
get_chapter_content가 제공한 챕터 text 전체를 읽고, 실제 서브 챕터의 제목·순서·
계층만 section_inventory로 작성하세요. 아직 내용을 요약하거나 문제를 만들지 마세요.

[책 정보]
{book_info_block}

{input_mode_block}

{section_inventory_guidelines_block}

[출력 형식 — JSON]
반드시 다음 스키마의 JSON 객체 하나만 반환하세요. 코드펜스는 사용하지 마세요.

{section_inventory_json_example}
"""

_SUMMARY = """\
당신은 PDF 학습 자료를 만드는 어시스턴트입니다.
원문 언어와 무관하게 주어진 챕터 text와 별도 분석자가 만든 section_inventory를
함께 읽고 한국어 summary와 key_points의 초안을 생성하세요. 문제는 만들지 마세요.
section_inventory는 원문 구조만 나타내며 요약 범위를 제한하지 않습니다. 각
section의 원문 전체를 직접 읽어 학습용 설명을 작성하세요. summary를 쓰기 전에
section_inventory.sections를 처음부터 끝까지 순회 계획으로 삼고, 명시적인 서브
챕터 각각에 대응하는 Markdown 제목과 설명이 빠짐없이 생성되도록 하세요.
학습자 정보와의 관련성으로 내용을 고르거나 버리지 마세요. 이 summary는 특정 관심사에
맞춘 발췌가 아니라 PDF 내용을 최대한 보존해 복습하는 **학습용 요약**입니다.
문제 생성기는 검토를 통과한 이 요약만 입력으로 받아 별도로 실행됩니다.

[책 정보]
{book_info_block}

{input_mode_block}

{summary_format_block}

{semantic_completeness_block}

[출력 형식 — JSON]
반드시 다음 스키마의 **JSON 객체 하나만** 반환하세요. 객체 전체를 감싸는
코드펜스(```)는 금지하지만, summary 값 **안에서는** 마크다운(코드블록 포함)을
자유롭게 쓰세요. summary의 줄바꿈은 **실제 줄바꿈(개행)**으로 넣으세요 —
`\\n` 같은 글자를 직접 타이핑하지 마세요(JSON 직렬화는 도구가 알아서 합니다).
summary에는 한국어 요약을 넣으세요.

{summary_only_json_example}

아직 save_chapter_result를 호출하지 마세요. review_prompt가 text와 이 초안의 의미
보존을 대조해 `passed`를 반환한 뒤에만 basic_question_prompt로 넘어갑니다.
"""


_BASIC_QUESTIONS = """\
당신은 검토를 통과한 챕터 요약으로 학습 확인 문제를 만드는 어시스턴트입니다.
입력으로 전달받은 summary와 key_points만 읽으세요. 원문 text나 section_inventory는
입력으로 받거나 참조하지 마세요.

[학습자 컨텍스트]
{user_context_block}

[활성화된 기본 문제 유형]
{enabled_basic_types_block}

[원문 글자 수별 최대 문제 개수]
입력에 함께 전달된 source_char_count를 아래 표에 적용하세요. 글자 수는 문제 개수
상한 계산에만 사용하고 문제 내용의 근거로 사용하지 마세요.
{scales_table}

{question_guidelines_block}

[출력 형식 — JSON]
반드시 다음 스키마의 JSON 객체 하나만 반환하세요. 객관식은 `correct_answer`에 정답
하나, `incorrect_answers`에 오답 보기 배열을 넣으세요. `options`와 `answer_index`는
넣지 말고 정답 위치도 정하지 마세요. 서버가 저장할 때 한 번만 배치합니다.

{basic_questions_json_example}

비활성화된 유형은 해당 키를 빈 배열([])로 두고 키 자체는 유지하세요.
"""


# 기존 클라이언트가 summarizer_prompt만 읽어도 요약 기반 문제를 만들도록 유지하는
# 결합 프롬프트다. 새 workflow는 summary_prompt와 basic_question_prompt를 분리한다.
_SUMMARIZER_COMPAT = """\
당신은 PDF 학습 자료를 만드는 어시스턴트입니다.
주어진 챕터 text와 section_inventory로 먼저 summary와 key_points를 작성해 확정하세요.
section_inventory는 구조만 안내하며 요약 범위를 제한하지 않습니다. 각 section의 원문
전체를 직접 읽으세요. 그 다음 원문과 section_inventory를 다시 참조하지 말고,
방금 확정한 summary와 key_points만
근거로 객관식/단답형/주관식 문제를 만드세요.
학습자 컨텍스트는 요약의 내용 범위나 보존할 항목을 정하는 기준이 아닙니다. 먼저
학습자 컨텍스트와 겹치는 내용만 고르지 않은 전체 학습용 요약을 확정한 뒤, 문제의
난이도·표현·예시·관점에만 학습자 컨텍스트를 사용하세요.

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

{semantic_completeness_block}

{question_guidelines_block}

[출력 형식 — JSON]
반드시 다음 스키마의 **JSON 객체 하나만** 반환하세요. summary와 key_points를 먼저
완성한 뒤 questions를 채우세요. summary 값 안에서는 마크다운을 사용할 수 있습니다.
객관식은 `correct_answer`와 `incorrect_answers`를 사용하고 `options`와
`answer_index`는 넣지 마세요. 정답 위치는 서버가 저장할 때 배치합니다.

{summary_json_example}

비활성화된 유형은 해당 키를 빈 배열([])로 두세요. 키 자체는 유지합니다.
새 workflow에서는 이 호환 프롬프트 대신 summary_prompt와 basic_question_prompt를
분리해 사용합니다.
"""


_SUMMARY_REVIEW = """\
당신은 PDF 학습 요약의 독립 검토자입니다.
챕터 text 전체와 작성된 요약·핵심 포인트 초안을 직접 비교하세요.
형식이 채워졌다는 이유로 통과시키지 말고 의미 보존 여부를 판단하세요.
앞 단계의 section 구조·제목·순서·계층은 이 단계에서 다시 검증하지 마세요. 이
검토는 원문 의미의 중요한 누락과 왜곡만 판단합니다.

[검토 기준]
- 챕터 text 전체를 직접 읽었을 때 개념·관계·절차·조건·예외·근거·사례·주의점 등
  학습에 필요한 내용이 요약에 충분히 설명됐는가
- 챕터 전체의 흐름과 유의미한 정보가 반영됐는가
- 핵심 주장·개념·관계·절차·조건·예외·근거·사례·주의점 중 원문에서 중요한
  내용이 과도한 압축으로 사라지지 않았는가
- 원문의 의미가 단순화 과정에서 왜곡되거나 잘못 연결되지 않았는가
- 특정 글자 수나 원문 대비 비율은 통과 기준으로 사용하지 않습니다.

중요 누락이나 왜곡이 하나라도 있으면 `status`를 `needs_revision`으로 두고
missing_significant_content 또는 distortions에 구체적으로 적으세요. 작성자가 그
피드백을 반영해 초안을 고친 뒤 검토를 다시 수행해야 합니다.

챕터 전체에서 중요한 누락·왜곡이 없을 때만 `status=passed`를 반환하세요.

[출력 형식 — JSON]
반드시 다음 스키마의 JSON 객체 하나만 반환하세요. 코드펜스는 사용하지 마세요.

{summary_review_json_example}
"""


# ---------------------------------------------------------------------------
# Extension agent 템플릿
# ---------------------------------------------------------------------------

_EXTENSION = """\
당신은 챕터 학습을 한 단계 확장하는 어시스턴트입니다.
get_chapter_summary가 반환한 summary와 key_points, source_char_count만 입력으로
받아 응용/심화 문제를 만듭니다. 원문 text를 받거나 읽지 마세요.

[학습자 컨텍스트]
{user_context_block}

[활성화 — extension만 처리]
객관식/단답/주관식 문제는 만들지 마세요. 그건 다른 sub-agent의 책임입니다.

{extension_guidelines_block}

[최대 개수]
{scales_table}

[출력 형식 — JSON]
```text
{extension_json_example}
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
2) summary_pending_chapter_ids에 있으면 다음 순서를 지키기
   a. get_chapter_content(work_id, chapter_id)로 원문 text와 char_count 받기
   b. section_inventory_prompt로 text 전체의 실제 제목·순서·계층만 정리
   c. text와 section_inventory를 summary_prompt에 전달하고 각 section의 원문 전체를
      직접 읽어 summary·key_points 초안 작성
   d. 가능하면 작성자와 분리된 검토자가 review_prompt로 text와 초안의 의미를 대조
   e. needs_revision이면 요약 누락·왜곡 피드백을 반영해 고치고
      다시 검토(최대 2회)
   f. passed이면 별도 문제 생성 단계에 원문 text와 section_inventory를 전달하지 말고,
      summary·key_points·source_char_count만 basic_question_prompt에 전달
   g. 요약, 문제, section_inventory, summary_review를 하나의 payload로 합쳐
      save_chapter_result(work_id, chapter_id, data)로 저장
   검토가 통과하지 않으면 불완전한 초안을 저장하지 않기
3) extension_pending_chapter_ids에 있으면 요약 저장이 끝난 뒤
   get_chapter_summary(work_id, chapter_id)를 호출하고, 반환된 summary·key_points·
   source_char_count만 extension_prompt에 전달해 만든 결과를
   save_extension_result(work_id, chapter_id, data)로 저장
4) 두 목록 중 실제로 포함된 요청된 결과 유형만 저장하고 다음 챕터로 진행
실패 시 1회 재시도. 그래도 실패하면 다음 챕터로.
chapter_ids는 두 pending 목록의 자연 정렬된 합집합입니다.
"""

WORKFLOW_INSTRUCTIONS_PARALLEL = """\
최대 5개 챕터를 동시에 sub-agent로 디스패치하세요.
- 각 sub-agent는 chapter_id가 summary_pending_chapter_ids와
  extension_pending_chapter_ids에 각각 포함되는지 먼저 확인합니다.
- summary pending이면 챕터별로 get_chapter_content → section_inventory_prompt →
  summary_prompt → review_prompt 순서를 지킵니다. review가 needs_revision이면
  요약 누락·왜곡 피드백을 반영해 최대 2회 다시 검토합니다.
  passed이면 원문과 section_inventory를 제외한 summary·key_points·
  source_char_count만 별도 basic_question_prompt에 전달합니다.
  요약·문제·검증 근거를 합쳐 save_chapter_result로 저장하고, 검토가 통과하지 않으면
  저장하지 않습니다.
- extension pending이면 같은 챕터의 요약 저장이 끝난 뒤 get_chapter_summary를
  호출합니다. 반환된 summary·key_points·source_char_count만 extension_prompt에
  전달해 save_extension_result로 저장합니다. 외부 검색은 사용하지 않습니다.
- 한 챕터 안의 요약 → 기본 문제 → 요약 저장 → 확장 문제 의존 순서를 지키고,
  서로 다른 챕터만 최대 5개까지 병렬 처리합니다.
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


def _format_enabled_basic_types(opts: dict[str, bool]) -> str:
    return "\n".join(
        line for line in _format_enabled_types(opts).splitlines()
        if "확장형" not in line
    )


def _format_user_context(uc: str) -> str:
    if not uc:
        return "(제공되지 않음)"
    return (
        uc
        + "\n위 컨텍스트는 문제의 난이도, 표현 수준, 예시, 관점을 맞추는 데만 "
        "사용하세요. 요약의 내용 범위나 보존할 원문을 제한하지 마세요."
    )


def build_prompts(state: dict[str, Any], book_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """state + book_info를 토대로 sub-agent 프롬프트 묶음 생성.

    Returns:
        {
            "mode": "sequential" | "parallel",
            "section_inventory_prompt": str,
            "content_map_prompt": str,      # 기존 키 존재 호환 alias
            "summary_prompt": str,
            "basic_question_prompt": str,
            "summarizer_prompt": str,        # 기존 클라이언트 호환용 결합 프롬프트
            "review_prompt": str,
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
    enabled_basic_types_block = _format_enabled_basic_types(opts)
    input_mode_block = INPUT_MODE_OCR if ocr_mode else INPUT_MODE_TEXT
    summary_json_example = json.dumps(
        question_contract.agent_summary_payload_example(), ensure_ascii=False, indent=2,
    )
    summary_only_json_example = json.dumps(
        {
            "summary": "한국어 마크다운 요약",
            "key_points": ["핵심 포인트 1", "핵심 포인트 2"],
        },
        ensure_ascii=False,
        indent=2,
    )
    basic_questions_json_example = json.dumps(
        {
            "questions": question_contract.agent_summary_payload_example()["questions"],
        },
        ensure_ascii=False,
        indent=2,
    )
    section_inventory_json_example = json.dumps(
        summary_contract.section_inventory_example(), ensure_ascii=False, indent=2,
    )
    summary_review_json_example = json.dumps(
        summary_contract.summary_review_example(), ensure_ascii=False, indent=2,
    )
    section_inventory_prompt = _SECTION_INVENTORY.format(
        book_info_block=book_info_block,
        input_mode_block=input_mode_block,
        section_inventory_guidelines_block=SECTION_INVENTORY_GUIDELINES,
        section_inventory_json_example=section_inventory_json_example,
    )
    summary_prompt = _SUMMARY.format(
        book_info_block=book_info_block,
        input_mode_block=input_mode_block,
        summary_format_block=SUMMARY_FORMAT,
        semantic_completeness_block=SEMANTIC_COMPLETENESS_GUIDELINES,
        summary_only_json_example=summary_only_json_example,
    )
    basic_question_guidelines = BASIC_QUESTION_GUIDELINES.format(
        question_self_containedness_block=QUESTION_SELF_CONTAINEDNESS_GUIDELINES,
    )
    basic_question_prompt = _BASIC_QUESTIONS.format(
        user_context_block=user_context_block,
        enabled_basic_types_block=enabled_basic_types_block,
        scales_table=QUESTION_SCALES_TABLE,
        question_guidelines_block=basic_question_guidelines,
        basic_questions_json_example=basic_questions_json_example,
    )
    summarizer_prompt = _SUMMARIZER_COMPAT.format(
        book_info_block=book_info_block,
        user_context_block=user_context_block,
        enabled_types_block=enabled_types_block,
        scales_table=QUESTION_SCALES_TABLE,
        input_mode_block=input_mode_block,
        summary_format_block=SUMMARY_FORMAT,
        semantic_completeness_block=SEMANTIC_COMPLETENESS_GUIDELINES,
        question_guidelines_block=basic_question_guidelines,
        summary_json_example=summary_json_example,
    )
    review_prompt = _SUMMARY_REVIEW.format(
        summary_review_json_example=summary_review_json_example,
    )

    extension_prompt: str | None = None
    if opts.get("extension"):
        extension_json_example = json.dumps(
            question_contract.extension_payload_example(), ensure_ascii=False, indent=2,
        )
        extension_guidelines = EXTENSION_GUIDELINES.format(
            question_self_containedness_block=QUESTION_SELF_CONTAINEDNESS_GUIDELINES,
        )
        extension_prompt = _EXTENSION.format(
            user_context_block=user_context_block,
            scales_table=QUESTION_SCALES_TABLE,
            extension_guidelines_block=extension_guidelines,
            extension_json_example=extension_json_example,
        )

    workflow = (
        WORKFLOW_INSTRUCTIONS_PARALLEL
        if mode == "parallel"
        else WORKFLOW_INSTRUCTIONS_SEQUENTIAL
    )
    if ocr_mode:
        ocr_note = (
            "[OCR 모드] get_chapter_content는 set_chapters에서 PaddleOCR CPU로 "
            "선계산한 본문 text를 돌려줍니다. 원문 text는 요약 생성·검토에만 "
            "사용하고 문제 생성 단계에는 전달하지 마세요.\n\n"
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
        "section_inventory_prompt": section_inventory_prompt,
        # 기존 키를 바로 제거하지 않고 새 inventory 프롬프트의 alias로 제공한다.
        "content_map_prompt": section_inventory_prompt,
        "summary_prompt": summary_prompt,
        "basic_question_prompt": basic_question_prompt,
        "summarizer_prompt": summarizer_prompt,
        "review_prompt": review_prompt,
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
