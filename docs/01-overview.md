# 01. 프로젝트 개요

## 무엇을 만드는가

PDF(주로 책 스캔본)를 챕터별 요약 + 4유형 검증 문제로 변환하고, 정적 학습 자료 폴더로 출력하는 MCP 서버.

- 책 메타 정보(제목, 저자, 서문 요약)도 함께 정리하여 학습 자료에 포함
- 학습 진도는 로컬 JSON 파일로 자동 추적
- 진도 read/write를 위한 경량 launcher(`study_html.py`)도 함께 생성

## 기술 스택

- Python 3.10+
- 의존성: `mcp`, `pymupdf`, `pillow`, `rich`, `markdown-it-py` (모두 순수 pip 패키지)
  - `rich`는 md_tui(터미널 TUI) 출력 전용. MCP 서버 설치 시 venv에 함께 깔리므로,
    학습 런처를 **서버와 같은 인터프리터**로 실행하면 추가 설치가 필요 없다.
  - `markdown-it-py`는 요약 마크다운 → HTML 변환(HtmlRenderer)에 쓴다. **`rich`가
    이미 끌어오는 전이 의존성**이라 실제 설치 패키지는 늘지 않아 **사용자가 따로
    설치할 게 없다**. 만에 하나 없더라도 HtmlRenderer가 **내장 폴백 변환기**
    (`_FallbackMd`)로 떨어져 마크다운을 그대로 텍스트로 노출하지 않는다(설치 강요 X).
- 시스템 의존성: 없음
- MCP SDK: FastMCP (`from mcp.server.fastmcp import FastMCP`)

## 책임 분담

| 주체 | 역할 |
|---|---|
| pdf-study MCP (우리) | PDF 처리, 챕터 분리, (OCR 모드) 페이지→이미지 렌더, 학습 자료 렌더링 |
| 메인 LLM (Claude/GPT/Gemini) | 챕터 요약, 4유형 문제 생성, 책 정보 추출, 본문/OCR 오류 자연 교정. **OCR 모드에선 sub-agent가 페이지 이미지를 직접 읽어 본문을 OCR** |
| Exa Web Research MCP (HTTP 내부 흡수) | 확장 문제용 외부 검색 (API key 불필요) |
| study_html.py (생성물) | 학습 시 localhost 서버 + 진도 read/write |

## 사용자 노출 정책

- 사용자가 등록하는 MCP: **pdf-study 1개**
- 외부 MCP(Exa)는 우리 MCP가 내부에서 HTTP로 호출 → 사용자에게 비공개
- pdf-mcp 같은 외부 의존성 없음 (PyMuPDF 직접 사용)

## 사용자 셋업

```bash
git clone <repo-url> ~/pdf-study
cd ~/pdf-study
./scripts/setup_mcp.sh
```

스크립트는 프로젝트 로컬 `.venv`에 의존성을 설치하고, MCP 클라이언트에 붙여 넣을
절대경로 설정을 출력한다. 전역 Python이나 다른 프로젝트의 `.venv`를 사용하지 않는다.

```json
// claude_desktop_config.json (또는 호환 클라이언트)
{
  "mcpServers": {
    "pdf-study": {
      "command": "<PDF_STUDY_INSTALL_DIR>/.venv/bin/python",
      "args": ["-m", "pdf_study"]
    }
  }
}
```

`<PDF_STUDY_INSTALL_DIR>`는 `pyproject.toml`이 있는 이 저장소의 절대경로다. 실제 값은
`scripts/setup_mcp.sh`가 출력한 JSON을 그대로 복사한다.
