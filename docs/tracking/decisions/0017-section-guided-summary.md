# section inventory는 요약 생성에 강제하고 이후 구조 검증은 생략한다

## 상태

부분 대체 — 2026-08-12 채택, source 분할 방식은 0018로 대체됨

## 배경

긴 챕터를 읽고 요약하는 agent는 이미 분석된 section 구조를 초안에 옮기는 과정에서도
실수할 수 있다. 기존 흐름은 요약 뒤 독립 검토와 서버 저장 경계에서 inventory 자체,
section별 review, Markdown heading 포함 여부, raw 번호 계층을 다시 검증했다. 이
후행 구조 검증은 실제 요약 품질보다 구조 재판정의 불확실성 때문에 많은 챕터를
실패시키는 원인이 됐다.

## 결정

- `section_inventory` 생성 단계는 유지한다. inventory는 실제 제목·순서·상대 계층만
  기록하고 내용 선별에는 사용하지 않는다.
- 요약 프롬프트는 inventory의 `sections`를 순서대로 순회하고, 모든 명시적 서브
  챕터의 `heading`과 `level`·`parent_id` 계층을 Markdown 제목으로 반드시 반영하게
  한다. 각 제목 아래에는 해당 section의 원문 전체를 읽은 학습 설명을 작성한다.
- 독립 검토에는 원문과 요약·핵심 포인트 초안만 전달한다. 검토자는 챕터 전체의 중요
  내용 누락과 왜곡을 판단하며 section 구조·제목·순서·계층은 다시 검증하지 않는다.
- `summary_review.reviewed_against`는 `chapter_text`, `draft_summary`만 요구한다.
  `section_reviews`는 더 이상 생성하거나 검증하지 않는다. 구형 payload에 들어오면
  호환을 위해 받아들이되 canonical 저장 전 제거한다.
- 저장 경계는 `section_inventory` 객체가 생성 증거로 존재하는지만 확인한다. 내부
  구조, raw 번호형 제목과의 일치, 최종 Markdown heading 포함 여부는 재검증하지 않는다.
- 챕터 전체의 `summary_review.status=passed`, 빈 중요 누락·왜곡 배열, 요약·핵심
  포인트·활성 문제의 기존 필수 검증은 유지한다.

## 결과

구조를 발견하는 책임은 inventory 분석 단계에, 발견된 구조를 사용하는 책임은 요약
생성 단계에 집중된다. 이후 단계는 같은 구조를 다시 추론하지 않아 false negative를
줄인다. 대신 서버가 section 누락을 독립적으로 차단하지 않으므로, 요약 프롬프트의
명시적 순회 지시와 전체 원문 의미 검토가 품질을 책임진다.
