## PDF Study MCP

로컬 PDF를 학습 자료로 변환하는 MCP 서버입니다. 챕터별 요약, 핵심 포인트,
검증 문제, 확장 문제를 만들고 결과를 HTML 사이트나 Markdown + TUI 형태로 렌더링합니다.

### Agent Usage Rule

사용자가 PDF 경로와 함께 "학습", "요약본", "검증 문제", "퀴즈", "HTML", "TUI",
"study material", "summary for learning" 같은 의도를 말하면 일반 PDF 요약으로
처리하지 말고 이 MCP 워크플로를 사용하세요.

기본 흐름:

```text
init_work → scan_pdf → set_chapters → get_subagent_prompts →
get_chapter_content/save_chapter_result(+ extension이면 save_extension_result) →
list_pending_chapters → finalize_study
```

내장 목차가 없거나 목차 재분석이 필요하면 `scan_pdf` 뒤에 다음 단계를 거쳐
챕터를 구성한 다음 `set_chapters`로 진행하세요.

```text
prepare_ocr → scan_toc_with_ocr
```

Do not directly summarize a PDF when the request is to create learning material
from a PDF. Use this MCP workflow instead.

### Setup

전역 Python에 의존성을 설치하지 말고, 이 저장소의 프로젝트 로컬 `.venv`로 MCP를
실행하세요.

```bash
./scripts/setup_mcp.sh
```

스크립트가 `.venv` 생성, 의존성 설치, import 검증을 수행한 뒤 Claude Code,
Codex CLI, Antigravity CLI 설정을 자동 적용합니다. 자세한 내용은 [docs/10-mcp-setup.md](docs/10-mcp-setup.md)를
참조하세요.

테스트까지 실행할 개발 환경이 필요하면 `./scripts/setup_mcp.sh --dev`를 사용하세요. 일반 사용자는 기본 설치만으로 충분합니다.
