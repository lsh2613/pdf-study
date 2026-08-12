# 결정 기록

- `0001-local-mcp-server.md` - 로컬 MCP 서버 하나가 PDF 처리와 렌더링을 맡는다.
- `0002-chapter-boundaries.md` - 챕터 경계는 내장 목차 또는 목차 페이지 이미지에서만 얻는다.
- `0003-text-or-ocr.md` - 본문 입력은 text 모드와 OCR 모드를 명시적으로 나눈다.
- `0004-neutral-render-data.md` - HTML과 Markdown+TUI는 같은 중립 JSON에서 만든다.
- `0005-extension-search.md` - 확장 문제 검색 실패 정책. 0007 결정으로 대체됐다.
- `0006-project-local-venv.md` - MCP 실행은 저장소 안 `.venv`를 기준으로 한다.
- `0007-extension-without-search.md` - 확장 문제의 외부 검색을 제거한다. 직접 입력 근거는 0013에서 요약으로 변경됐다.
- `0008-managed-output-replacement.md` - 기존 출력은 명시적으로 선택하고 manifest 관리 경로만 교체한다. `new_output_dir` 선택은 0011로 대체됐다.
- `0009-request-context-and-elicitation.md` - agent workspace 기준 경로와 서버 주도 필수 선택을 사용한다. 경로 결정은 0011, 새 작업의 선택적 학습자 정보는 0014로 대체됐다.
- `0010-single-elicitation-entrypoint.md` - 선택형 워크플로는 Elicitation과 실행을 결합한 등록 함수 하나만 둔다.
- `0011-server-project-result-root.md` - 서버 프로젝트의 고정 result 루트와 읽기 전용 결과 조회를 사용한다.
- `0012-semantic-summary-coverage.md` - 요약은 고정 분량 대신 의미 누락·왜곡 검토로 완료를 판정한다. point 기반 내용 목록은 0015로 대체됐다.
- `0013-summary-grounded-questions.md` - 모든 문제는 검토를 통과한 챕터 요약만 내용 근거로 사용한다.
- `0014-required-user-context.md` - 새 작업은 문제 생성 조정에 사용할 비어 있지 않은 학습자 정보를 필수로 받는다.
- `0015-section-first-summary.md` - 원문 구조 inventory 뒤 각 section 전체를 직접 요약한다. section별 검토 결정은 0017로 대체됐다.
- `0016-source-structure-summary-gate.md` - 학습자 정보는 문제 조정에만 사용한다. raw 구조 저장 게이트는 0017로 대체됐다.
- `0017-section-guided-summary.md` - inventory를 요약 생성에 강제하고 이후 section 구조 재검증을 제거한다.
