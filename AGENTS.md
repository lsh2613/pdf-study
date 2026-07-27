# pdf-study 작업 기준

pdf-study는 로컬 PDF를 챕터별 학습 자료로 바꾸는 MCP 서버다. 사용자는 개발자로 한정하지 않는다. PDF 자체를 공부하거나 책을 PDF로 만들어 학습하려는 사람이, 챕터 요약·핵심 포인트·검증 문제·확장 문제를 HTML 또는 터미널 자료로 받는 것이 목표다.

```
.
├── CLAUDE.md -> Claude Code가 작업 전에 읽는 프로젝트 기준
├── AGENTS.md -> Codex가 작업 전에 읽는 프로젝트 기준
├── docs/
│   ├── architecture.md -> 서버 구성과 데이터 흐름
│   ├── business-rules.md -> PDF 학습 자료 생성 규칙
│   ├── security.md -> 로컬 PDF와 네트워크 경계의 보호 기준
│   ├── standards.md -> 변경 시 지켜야 하는 강제 규칙
│   ├── engineering-notes.md -> 놓치기 쉬운 동작과 확인 절차
│   ├── operations.md -> 설치·실행·검증 절차
│   ├── contracts.md -> MCP 도구와 출력물의 외부 계약
│   └── tracking/
│       ├── status.md -> 현재 구현 상태와 남은 일
│       ├── decisions/
│       │   ├── index.md -> 결정 기록 목록
│       │   ├── 0001-local-mcp-server.md -> 로컬 MCP 서버 형태
│       │   ├── 0002-chapter-boundaries.md -> 챕터 경계 판단 방식
│       │   ├── 0003-text-or-ocr.md -> 본문 추출 방식
│       │   ├── 0004-neutral-render-data.md -> 렌더러 공통 데이터
│       │   ├── 0005-extension-search.md -> 확장 문제 검색 처리
│       │   ├── 0006-project-local-venv.md -> 프로젝트 로컬 실행 환경
│       │   ├── 0007-extension-without-search.md -> 검색 없는 확장 문제
│       │   └── 0008-managed-output-replacement.md -> 출력 폴더 충돌과 교체
│       └── findings.md -> 아직 해결하지 않은 문제
├── server.py / analysis.py / workspace.py / prompts.py -> MCP 도구, 흐름 결정, 상태 저장, 프롬프트
├── pdf/AGENTS.md -> PDF 열기·목차·페이지 렌더링 기준
├── renderer/AGENTS.md -> HTML·Markdown/TUI 출력 기준
├── templates/AGENTS.md -> 생성물에 복사되는 런처와 정적 자산 기준
└── scripts/AGENTS.md -> 설치 스크립트 기준
```

## 반드시 지킬 일

- PDF 학습 요청은 일반 요약으로 처리하지 않는다. 기본 흐름은 `init_work → scan_pdf → set_chapters → get_subagent_prompts → save_* → list_pending_chapters → finalize_study`다. 내장 목차가 없거나 목차 재분석이 필요하면 `scan_pdf` 뒤에 `prepare_ocr → scan_toc_with_ocr`를 거쳐 챕터를 구성한 다음 `set_chapters`로 간다.
- `get_subagent_prompts`의 `summary_pending_chapter_ids`와 `extension_pending_chapter_ids`는 실제 남은 결과를 각각 담고, 호환용 `chapter_ids`는 두 목록의 자연 정렬 합집합만 담는다. 상태 판정은 `completed`·`skipped`만 done, `pending`·`failed`·`in_progress`는 pending이며, 확장 문제가 비활성이면 `extension_pending_chapter_ids`는 항상 빈 목록이다. 완료 챕터는 raw 검증과 처리·저장 안내에서 제외하며 workflow와 `next_action`은 실제 pending 결과 유형만 안내해야 한다.
- `init_work`는 단답형·주관식·확장형 문제 선택과 선택적 학습자 정보를 같은 MCP form elicitation으로 직접 받는다. 사용자가 form을 승인하기 전에는 작업을 만들거나 PDF 스캔으로 넘어가지 않는다. 객관식만 기존 호환을 위해 기본 활성이다.
- `init_work`가 고정 출력 폴더의 기존 관리 작업을 발견하면 `resume`, `replace`를 form elicitation으로 직접 확인한다. `replace`는 사용자의 명시적 선택을 받은 뒤에만 같은 등록 함수 안에서 실행한다. 관리되지 않은 파일이 있으면 실패한다. 렌더 결과 정리는 `.pdf-study-manifest.json`에 기록된 관리 경로에 한정하며 다른 사용자 파일을 삭제하거나 덮어쓰면 안 된다.
- 챕터 경계는 PDF 북마크 또는 목차 페이지 이미지로만 정한다. PDF 텍스트를 긁어 목차를 추정하는 코드를 추가하면 안 된다. `scan_pdf`는 목차 후보 이미지를 렌더할 뿐 OCR 모델을 준비하거나 실행하지 않는다.
- 챕터 추출 범위의 canonical 키는 PDF 파일의 1-based 범위를 담는 `pdf_pages`다. `source_pages`는 원문에 표시된 페이지 번호를 보존하는 선택적 메타이며 추출 범위를 바꾸지 않는다. 구형 `page_range`·`printed_range`는 기존 작업 읽기 호환에만 사용한다.
- 텍스트 레이어가 없거나 깨진 PDF는 text 모드로 밀어붙이면 안 된다. OCR 흐름은 `set_chapters`에서 PaddleOCR CPU로 본문을 선계산해 `chapters_raw/chN.json`의 `text`와 `char_count`로 저장하는 방향이다. `body_text`는 raw 본문을 덮어쓰는 경로로 쓰지 않는다.
- 사용자가 골라야 하는 선택지는 MCP form elicitation 안에서만 제공한다. 추천·기본값을 임의로 붙이거나 선택지를 합치면 안 된다.
- 사용자 선택값은 MCP form elicitation 응답으로만 받는다. 선택 파라미터와 구조화 fallback을 MCP 공개 계약에 다시 추가하면 안 된다. Elicitation 미지원 세션은 상태를 바꾸지 않고 실패해야 한다.
- 선택이 필요한 워크플로는 Elicitation과 승인 후 실행을 등록된 async MCP 함수 하나에서 처리한다. 같은 작업을 선택 인자로 직접 실행하는 별도 동기 함수, `_impl` 함수, MCP wrapper를 다시 추가하면 안 된다.
- 출력 폴더는 요청의 단일 Codex workspace 또는 단일 MCP file root 아래 `result/<pdf-name>`으로만 계산한다. MCP 서버 프로세스 cwd, 에이전트가 준 `output_dir`, 임의의 다른 workspace를 사용하면 안 된다.
- 확장 문제는 외부 검색 없이 챕터 본문과 학습자 정보만으로 만든다. 검색 도구나 HTTP 검색 클라이언트를 다시 추가하려면 별도의 보안·계약 결정을 먼저 기록해야 한다.
- `.work/state.json`은 잠금이 걸린 `workspace.py` 헬퍼로만 바꾼다. 직접 read-modify-write를 넣으면 병렬 처리에서 상태가 깨진다.
- `set_chapters`는 스캔 여부, 처리 모드, 챕터 정의와 책 정보 준비를 상태 변경 없이 먼저 검증한다. 검증된 모드·챕터와 처리 시작 phase는 하나의 잠금 구간에서 확정하고, 이후 본문 추출 실패는 새 설정의 `chapter_processing=failed`로 남긴다. 같은 작업의 `set_chapters` 전체 처리는 직렬화하며 책 정보 rollback 실패를 숨기면 안 된다.

## 작업 전 확인

- MCP 도구 흐름이나 사용자 선택지를 바꿀 때는 `docs/contracts.md`와 `docs/business-rules.md`를 먼저 확인한다.
- PDF 페이지 번호, 목차, OCR, 텍스트 품질을 바꿀 때는 `pdf/AGENTS.md`를 먼저 확인한다.
- HTML 또는 TUI 출력, 진도 저장, 마크다운 렌더링을 바꿀 때는 `renderer/AGENTS.md`와 `templates/AGENTS.md`를 먼저 확인한다.
- 설치·실행 환경을 바꿀 때는 `scripts/AGENTS.md`와 `docs/operations.md`를 먼저 확인한다.
- 상태 파일, 저장 폴더, 동시 저장을 바꿀 때는 `docs/engineering-notes.md`의 상태 저장 항목을 확인한다.

## 바로 알려야 하는 문제

- 사용자의 PDF 본문이나 생성 결과가 의도와 다르게 외부로 전송될 수 있는 변경
- 챕터 저장이 완료로 표시됐지만 요약·문제 파일이 비어 있거나 일부만 저장되는 상황
- `finalize_study`가 처리되지 않은 챕터를 조용히 제외하고 결과물을 만드는 상황
- 같은 PDF 작업에서 병렬 저장 후 `state.json`이 깨지거나 완료 상태가 되돌아가는 상황
- 기존 MCP 도구의 입력·출력·에러 형태가 바뀌어 클라이언트가 기존 흐름을 이어갈 수 없는 변경
