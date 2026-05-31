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
| 9 | [09-implementation-plan.md](./09-implementation-plan.md) | 단계별 구현 계획, 테스트 전략, ROADMAP |
| 10 | [10-internal-flow.md](./10-internal-flow.md) | 사용자 발화 → MCP 도구 흐름 단계별, 파일·함수 매핑, 디스크 데이터 흐름 |

## 빠른 시작

새 Claude Code 세션에서:

> docs/ 폴더의 가이드를 참고해 Phase 1부터 단계별로 빌드해주세요.
> 한 Phase 끝날 때마다 결과를 확인할 수 있게 진행하고,
> 다음 Phase로 넘어가도 되는지 물어봐 주세요.

Phase별로 끊어서:

> docs/09-implementation-plan.md의 Phase 1만 먼저 구현해주세요.
> pdf/ 모듈 4개 파일 + lang.py.
> 작은 샘플 PDF로 테스트한 다음 Phase 2로 갑시다.

## 읽는 순서 권장

처음 보는 경우: 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09

구현 들어가는 경우: 09(전체 흐름) → 해당 Phase 관련 문서만 참조

이미 구현된 시스템을 이해하려는 경우: 01(개요) → **10(내부 동작 흐름)** → 필요 시 02·03·05 참조
