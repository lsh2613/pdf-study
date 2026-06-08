# 08. 아키텍처 & 코딩 가이드

## 패키지 구조

```
pdf_study/
├── __init__.py
├── __main__.py             # python -m pdf_study 진입점
├── server.py               # FastMCP 도구 12개 (resume_work 포함)
├── pdf/
│   ├── __init__.py
│   ├── reader.py           # PyMuPDF: 메타·offset·get_outline(내장 목차)·locate_toc_pages·render_pages(→JPEG)
│   ├── toc_finder.py       # 본문 내 목차 정규식 탐색 (메인 흐름에서 미사용 — 레거시)
│   └── chapter.py          # 챕터 분할 (set_chapters 처리)
├── analysis.py             # scan_pdf, set_chapters 통합 로직
├── workspace.py            # .work/ 폴더 관리, state.json read/write + lock
├── prompts.py              # sub-agent 시스템 프롬프트 (user_context + language 주입)
├── exa_client.py           # Exa Web Research MCP 호출 (API key 불필요)
├── lang.py                 # 본문 언어 감지 휴리스틱
├── renderer/
│   ├── __init__.py
│   ├── base.py             # Renderer 인터페이스 (render(work_id, out_dir))
│   ├── html_renderer.py    # 요약 마크다운→HTML(markdown-it). 그림은 다루지 않음
│   └── md_tui_renderer.py  # 챕터별 폴더 + summary.md + quiz.json + TUI launcher
└── templates/
    ├── html/
    │   ├── index.html      # 책 정보 + 챕터 목록 + 진도
    │   ├── chapter.html    # 챕터 본문 + 4유형 문제
    │   ├── style.css       # 최소 스타일
    │   ├── grading.js
    │   ├── storage.js
    │   ├── study_html.py       # 정적 서버 + 진도 API launcher (→ 출력 루트로 복사)
    │   └── README.md
    └── md_tui/
        ├── study_tui.py        # rich 기반 학습 TUI 엔진 (출력 루트에 복사, rich 자동설치)
        ├── chapter_launcher.py # 챕터별 thin shim (각 ch*/study_tui.py로 복사)
        └── README.md
```

## Renderer 인터페이스

```python
# renderer/base.py
from abc import ABC, abstractmethod
from pathlib import Path

class Renderer(ABC):
    @abstractmethod
    def render(self, work_id: str, output_dir: Path) -> None:
        """워크스페이스의 chapters/{summaries,quiz,extension_questions}/, book_info.json,
        state.json을 읽어 output_dir에 학습 자료를 생성한다."""

# server.py에서:
RENDERERS = {"html": HtmlRenderer, "md_tui": MdTuiRenderer}

def finalize_study(work_id, output_format="html", keep_work_dir=True, force=False):
    # force=False면 pending 챕터가 남아 있을 때 거부 (조용한 부분 렌더 방지)
    renderer = RENDERERS[output_format]()
    renderer.render(work_id, output_dir(work_id))
    if not keep_work_dir:
        shutil.rmtree(work_dir(work_id))
```

## 코딩 가이드라인

- 모든 도구는 `{ok, error, data, next_action}` 형식으로 응답
- 에러는 raise하지 말고 응답에 명시 (MCP 통신 안정성)
- `Path` 객체 사용 (str보다 안전)
- 한국어 파일 입출력: `encoding='utf-8'` 명시, JSON은 `ensure_ascii=False`
- 로깅: `logging.getLogger(__name__)` 모듈별 logger
- 비동기는 Exa 호출에만 (`async def search_extension_context`)
- 타입 힌트 적극 활용 (FastMCP가 input_schema 자동 생성)
- 도구 docstring에 명확한 사용법과 다음 단계 안내 포함
- state.json 수정은 반드시 `workspace.py`의 lock-protected 헬퍼를 통해서만 ([06-concurrency.md](./06-concurrency.md))
- 페이지 인덱스는 `pdf/reader.py` 경계에서만 0-based로 변환, 그 외 모듈은 1-based만 ([03-pdf-processing.md](./03-pdf-processing.md))
