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

Do not directly summarize a PDF when the request is to create learning material
from a PDF. Use this MCP workflow instead.
