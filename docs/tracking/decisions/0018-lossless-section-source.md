# canonical raw를 section별 무손실 source로 결합한다

## 상태

채택 — 2026-08-14

## 배경

제목·순서·계층만 가진 inventory를 전체 chapter text와 함께 요약자에게 주면, 요약자가
긴 원문에서 각 제목의 본문 범위를 다시 찾아야 한다. 구조를 한 번 분석했어도 본문과
section의 연결을 요약 단계에서 다시 추론하므로 누락·오배치 위험이 남는다. 반대로
분석 agent가 section 본문 전체를 JSON으로 복사하면 출력 truncation·변형·중복과 큰
토큰 비용이 새로운 단일 실패점이 된다.

## 결정

- inventory 분석자는 제목·순서·계층과 함께 명시적 section마다
  `source_anchor={text, occurrence}`를 기록한다. text는 canonical raw 줄 시작에서
  시작하고 줄 경계에서 끝나는 full-line exact
  문자열이고 occurrence는 같은 anchor의 1-based 출현 번호다.
- 분석 agent는 section 본문을 복사하지 않는다.
- `get_section_content`가 anchor를 canonical raw 문자 offset으로 변환한다. 첫 제목 앞
  내용은 `preamble`, 각 제목부터 다음 제목 전까지는 해당 section, 마지막은 raw 끝까지
  포함한다. 서브 챕터가 없으면 chapter 전체가 region 하나다.
- 반환한 `structured_sections[].source_text`를 순서대로 이어 붙이면 canonical raw와
  완전히 같아야 한다. span은 빈틈·중복 없이 0부터 raw 길이까지 이어진다.
- 요약 입력에는 전체 raw와 metadata-only inventory 대신 structured sections와 span이
  결합된 inventory를 전달한다. 전체 raw는 최종 의미 보존 review에서만 다시 사용한다.
- inventory의 `source_binding`에는 algorithm version, raw 글자 수·SHA-256과 region
  span을 기록한다. 저장 시 prepared binding이 있으면 이 무손실성만 기계적으로
  재검증한다. 구형 binding 없는 inventory는 입력 호환을 위해 허용한다.
- 최종 Markdown heading 포함 여부나 inventory 구조의 의미는 저장 단계에서 다시
  판단하지 않는다. 문제 생성에는 원문·structured sections·inventory를 전달하지 않는다.

## 결과

요약자는 section 경계를 다시 찾지 않고 서버가 제공한 canonical source region을 직접
요약한다. 원문 복사본은 저장 inventory에 남지 않는다. 반복 목차 제목이나 OCR 줄바꿈
때문에 anchor를 찾지 못하거나 순서가 뒤집히면 요약 전에 명시적으로 실패해 inventory를
다시 분석한다. section별 지역 정확도를 얻으면서 챕터 전체 의미 review로 section 사이의
관계·전제·결론도 계속 확인한다.
