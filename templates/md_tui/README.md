# 학습 자료 (Markdown + TUI)

PDF를 챕터별 요약 + 검증 문제로 변환한 결과입니다.

## 구성

- `book.md` — 책 정보와 챕터 목차
- `ch*/summary.md` — 챕터별 요약 (읽기용)
- `ch*/quiz.json` — 검증 문제 데이터 (TUI가 사용)
- `study.py` — 학습 TUI 엔진
- `ch*/study.py` — 해당 챕터로 바로 진입하는 launcher

## 실행

먼저 rich를 설치하세요:

    pip install rich

전체 챕터 선택 메뉴:

    python study.py

특정 챕터로 바로 진입:

    cd ch1 && python study.py

풀이 기록은 각 챕터 폴더의 `progress.json`에 자동 저장되어, 다시 실행하면
이어서 진행할 수 있습니다.
