# 08. 아키텍처 & 코딩 가이드

## 패키지 구조

```
pdf_study/
├── __init__.py
├── __main__.py             # python -m pdf_study 진입점
├── server.py               # FastMCP 도구 11개
├── pdf/
│   ├── __init__.py
│   ├── reader.py           # PyMuPDF + 텍스트 추출 + 품질 평가 + book metadata
│   ├── toc_finder.py       # 본문 내 목차 패턴 탐색
│   ├── chapter.py          # 챕터 분할 (set_chapters 처리)
│   └── images.py           # 이미지 추출 + 리사이즈
├── analysis.py             # scan_pdf, set_chapters 통합 로직
├── workspace.py            # .work/ 폴더 관리, state.json read/write + lock
├── prompts.py              # sub-agent 시스템 프롬프트 (user_context + language 주입)
├── exa_client.py           # Exa Web Research MCP 호출 (API key 불필요)
├── lang.py                 # 본문 언어 감지 휴리스틱
├── renderer/
│   ├── __init__.py
│   ├── base.py             # Renderer 인터페이스 (render(work_id, out_dir))
│   ├── html_renderer.py    # 현재 구현
│   └── md_tui_renderer.py  # 추후 (ROADMAP) — 인터페이스만 stub
└── templates/
    └── html/
        ├── index.html      # 책 정보 + 챕터 목록 + 진도
        ├── chapter.html    # 챕터 본문 + 4유형 문제
        ├── style.css       # 최소 스타일
        ├── grading.js
        ├── storage.js
        ├── serve.py
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
        """워크스페이스의 chapters/, extensions/, book_info.json,
        state.json을 읽어 output_dir에 학습 자료를 생성한다."""

# server.py에서:
RENDERERS = {"html": HtmlRenderer, "md_tui": MdTuiRenderer}

def finalize_study(work_id, output_format="html", keep_work_dir=True):
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
