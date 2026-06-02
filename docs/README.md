# pdf-study 구현 가이드

PDF(주로 책 스캔본)를 챕터별 요약 + 4유형 검증 문제로 변환하고, 정적 학습 자료 폴더로 출력하는 MCP 서버.

## 문서 구성

| # | 파일 | 내용 |
|---|---|---|
| 1 | [01-overview.md](./01-overview.md) | 프로젝트 개요, 기술 스택, 책임 분담, 사용자 셋업 |
| 2 | [02-mcp-api.md](./02-mcp-api.md) | MCP 도구 시그니처, 응답 형식, 메인 LLM 워크플로 |
| 3 | [03-pdf-processing.md](./03-pdf-processing.md) | PDF 처리 정책, 책 메타, 챕터 분리, 목차 감지, 언어 감지, 페이지 인덱스 |
| 4 | [04-content-generation.md](./04-content-generation.md) | 4유형 문제, user_context, Sub-agent 패턴 |
| 5 | [05-data-schemas.md](./05-data-schemas.md) | 데이터 스키마, 폴더 구조, work_id 규칙 |
| 6 | [06-concurrency.md](./06-concurrency.md) | 동시성 처리 (병렬 모드) |
| 7 | [07-study-ui.md](./07-study-ui.md) | serve.py, 진도 시스템, UI 동작 |
| 8 | [08-architecture.md](./08-architecture.md) | 패키지 구조, Renderer 인터페이스, 코딩 가이드 |
| 9 | [09-internal-flow.md](./09-internal-flow.md) | 사용자 발화 → MCP 도구 흐름 단계별, 파일·함수 매핑, 디스크 데이터 흐름 |

## 빠른 시작

구현은 완료된 상태다 (MVP 전 기능 + resume_work / finalize 완료 가드).
새 세션에서 기능을 추가·개선하려면:

> docs/09-internal-flow.md로 전체 흐름을 먼저 파악한 다음,
> 손댈 단계의 관련 문서(02 API · 03 PDF · 05 스키마 등)를 참조해
> 해당 모듈만 수정해 주세요. 변경 후 `pytest`로 회귀를 확인합니다.

## 읽는 순서 권장

처음 보는 경우: 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09

이미 구현된 시스템을 이해/수정하려는 경우:
01(개요) → **09(내부 동작 흐름)** → 필요 시 02·03·05·06 참조
