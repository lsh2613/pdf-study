# section 후보 감사와 조건부 구조 검토

## 상태

채택 — 2026-08-14

## 배경

section inventory 프롬프트가 실제 제목을 모두 보존하라고 명시해도 분석 agent가
`has_explicit_subchapters=false`를 반환하면 기존 서버는 원문 전체를 정상 section 하나로
결합했다. exact anchor와 무손실 span 검증은 제출된 구조의 기계적 일관성만 확인하므로,
원문에 명백한 제목이 있는데 inventory가 이를 누락한 의미적 false negative를 잡지 못했다.

## 결정

- 서버는 canonical chapter text에서 단일 단계 또는 계층형 번호로 시작하는 full-line occurrence를
  `section_candidates` 감사 신호로 제공한다. 이 후보는 최종 section 판정이 아니다.
- inventory 분석자는 전체 text를 직접 읽어 번호 없는 제목과 OCR로 깨진 제목도 찾고,
  서버 후보는 실제 section anchor 또는 허용된 사유의 `candidate_exclusions`로 모두
  설명한다.
- 설명되지 않은 후보가 하나라도 있으면 `get_section_content`는 fail-closed한다.
- `has_explicit_subchapters=false`이거나 후보 제외가 하나라도 있으면 전체 text,
  inventory, 후보를 대조한 별도 `section_review`의 `passed` 결과를 요구한다.
- 검토에서 누락 section, 잘못 등록한 section, 계층 오류, 미해결 후보가 있으면 inventory를
  고쳐 다시 검토한다. 해결되지 않으면 챕터 전체 fallback으로 요약을 진행하지 않는다.
- 후보 제외와 section review는 요약 전 감사 자료이며, canonical span이 결합된 저장용
  inventory에는 남기지 않는다.
- 요약 후 의미 보존 review는 기존처럼 section 구조를 다시 판단하지 않는다.

## 결과

번호형 제목이 있는 챕터가 근거 없이 전체 chapter 하나로 통과하지 않는다. 의미 판단은
계속 AI가 담당하고 스크립트는 누락 감지와 exact occurrence 검증만 담당한다. 명시적
section이 없는 챕터도 독립 구조 검토를 통과하면 정상적으로 전체 chapter로 처리된다.
