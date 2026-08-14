# section별 설명 draft 뒤 챕터 요약을 통합한다

## 상태

채택 — 2026-08-14

## 배경

`get_section_content`가 canonical raw를 section별로 무손실 분할해도 한 번의 생성 호출에
긴 챕터의 모든 source text와 수십 개 Markdown section 출력을 함께 요구하면 모델이 실제
내용을 설명하지 않고 같은 메타 문구를 반복할 수 있다. 프롬프트에 전체 원문을 읽으라는
경고를 추가하는 것만으로는 한 호출의 입력·출력 부담이 줄지 않는다.

## 결정

- 기본 생성 순서는 `section inventory → canonical section source → section draft 반복 →
  chapter synthesis → semantic review → questions`로 한다.
- `section_summary_prompt`는 보통 region 경계를 유지한 한 개 또는 서로 인접한
  작은 묶음만 받는다. 단, `kind=chapter`를 포함한 큰 단일 region은 안정적인
  문단 또는 full-line 경계에서 순서대로 무손실 fragment로 분할할 수 있다.
  fragment는 원래 kind·section_id를 유지하고 `fragment_index`·`fragment_count`·
  조정된 `source_span`으로 원래 region 전체를 빈틈·중복 없이 덮는다. 각 생성
  단위의 source text를 직접 읽고 실제 개념·관계·절차·조건·예외·비교·근거·
  사례·주의사항을 설명하는 draft를 하나씩 만든다.
- 제목이나 주제를 다시 말하는 메타 문구는 section 설명으로 사용하지 않는다.
- `chapter_synthesis_prompt`는 모든 section draft와 결합된 inventory만 받는다. 원문 text와
  source text를 다시 받지 않으며 draft의 의미를 유지한다. 같은 section_id의 여러
  fragment draft는 fragment_index 순서대로 하나의 inventory 제목 아래 재결합하고,
  원래 제목·순서·계층의 Markdown과 챕터 전체 key points를 만든다.
- section 묶음의 크기는 생성 입력 문맥이 안전한 범위에서 정한다. 큰 단일
  region의 예외적 분할은 안정적인 문단 또는 full-line 경계만 쓰고 임의의 글자
  위치에서 자르지 않는다. 요약 분량이나 원문 대비 압축률 기준은 도입하지 않는다.
- 기존 `summary_prompt`와 `summarizer_prompt`는 구형 클라이언트 호환용으로 유지한다.
- section inventory는 챕터 첫머리의 본문 없는 연속 제목 블록과 뒤에서 본문과 함께
  반복되는 제목을 구분하고, 번호 구성요소 깊이를 계층의 강한 단서로 사용한다.

## 결과

모델은 한 번에 챕터 전체 구조와 긴 최종 출력을 처리하는 대신 작은 source 범위에서
실제 설명을 먼저 완성한다. 통합 단계는 이미 근거가 확인된 draft를 조립하는 데 집중한다.
서버가 요약 의미를 새로 판정하는 검증을 추가하지 않고 생성 작업의 형태를 바꿔
placeholder 요약 가능성을 낮춘다.
