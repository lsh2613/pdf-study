# renderer 모듈 기준

## 범위

이 모듈은 `.work`에 저장된 책 정보, 챕터 상태, 요약, 기본 문제, 확장 문제, raw 데이터를 읽어 최종 학습 자료를 만든다. PDF 읽기, 요약 생성, 문제 생성은 이 모듈의 책임이 아니다.

## 경계

렌더러는 저장된 중립 JSON을 표시 형식으로 바꾸는 계층이다. 렌더링 중 PDF 파일을 다시 열거나, 누락된 요약을 생성하거나, 문제 내용을 고치면 안 된다.

HTML과 Markdown+TUI는 같은 로더 관점을 공유해야 한다. 한쪽에서 skip 챕터를 제외하거나 확장 문제를 합치는 규칙이 바뀌면 다른 쪽도 같은 저장 의미를 따라야 한다.

개별 렌더러는 최종 출력 폴더를 직접 정리하지 않고 전달받은 비어 있는 staging 폴더에 현재 세대만 만든다. `output_manager.py`가 `.pdf-study-manifest.json`에 기록된 관리 경로를 교체하고 실패 시 이전 세대를 복원한다. manifest 밖의 파일은 렌더러나 output manager가 삭제하거나 덮어쓰면 안 된다.

## 지켜야 할 동작

- skip 챕터는 출력 목차와 챕터 페이지에 나타나면 안 된다.
- HTML은 챕터가 하나면 `main.html`, 여러 개면 `index.html`과 `chN.html`을 만든다.
- HTML 요약은 Markdown으로 렌더링한다. 변환 라이브러리가 없어도 최소 폴백이 원시 마크다운 노출을 막아야 한다.
- 리터럴 `\n`으로 이중 이스케이프된 요약은 실제 줄바꿈이 전혀 없을 때만 복구한다. 정상 코드블록이나 실제 개행이 있는 요약은 건드리지 않는다.
- Markdown+TUI 출력은 챕터별 `summary.md`와 `quiz.json`을 나눈다. summary 파일에 정답이나 모델 답안을 쓰면 안 된다.
- 중립 데이터 로더는 현재 `summary_status` 또는 `extension_status`가 `completed`인 결과 파일만 읽는다. `force=true`도 pending·failed 챕터의 같은 ID 예전 JSON을 읽는 근거가 아니다.
- 챕터 페이지는 공통 포맷터로 `pdf_pages`를 `PDF`, 선택적 `source_pages`를 `원문`으로 표시한다. HTML과 Markdown+TUI가 서로 다른 명칭이나 범위를 보여주면 안 된다.
- progress는 이전 manifest의 출력 형식과 학습 fingerprint가 현재 세대와 모두 같을 때만 staging에 복사한다.

## 테스트 기준

HTML 구조, 마크다운 변환, skip 제외, 단일/다중 챕터 분기, manifest 교체와 rollback은 `tests/test_renderer.py`를 확인한다. Markdown+TUI 파일 구조, quiz 병합, progress 호환성, rich 없는 평문 실행은 `tests/test_md_tui_renderer.py`를 확인한다.
