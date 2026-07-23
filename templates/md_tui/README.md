# 학습 자료 (Markdown + TUI)

PDF를 챕터별 요약 + 검증 문제로 변환한 결과입니다.

## 구성

- `book.md` — 책 정보와 챕터 목차
- `ch*/summary.md` — 챕터별 요약 (읽기용)
- `ch*/quiz.json` — 검증 문제 데이터 (TUI가 사용)
- `study_tui.py` — 학습 TUI 엔진
- `ch*/study_tui.py` — 해당 챕터로 바로 진입하는 launcher

## 실행

별도 준비 없이 바로 실행하세요. 의존성 `rich`가 없으면 **첫 실행 시 자동 설치를
시도**하고, 설치가 불가능한 환경(pip 부재·오프라인·권한·externally-managed 등)
이면 **평문 모드로 폴백**해 그래도 실행됩니다. 보기 좋은 화면을 원하면
`pip install rich` 후 다시 실행하세요.

전체 챕터 선택 메뉴:

    python study_tui.py

특정 챕터로 바로 진입:

    cd ch1 && python study_tui.py

풀이 기록은 각 챕터 폴더의 `progress.json`에 자동 저장됩니다. 완료 전 다시 실행하면
메뉴를 묻지 않고 첫 미응답 문제부터 자동으로 이어서 진행하며, 이미 답한 문제는 다시
묻지 않습니다.
