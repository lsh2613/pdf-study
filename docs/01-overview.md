# 01. 프로젝트 개요

## 무엇을 만드는가

PDF(주로 책 스캔본)를 챕터별 요약 + 4유형 검증 문제로 변환하고, 정적 학습 자료 폴더로 출력하는 MCP 서버.

- 책 메타 정보(제목, 저자, 서문 요약)도 함께 정리하여 학습 자료에 포함
- 학습 진도는 로컬 JSON 파일로 자동 추적
- 진도 read/write를 위한 경량 launcher(`serve.py`)도 함께 생성

## 기술 스택

- Python 3.10+
- 의존성: `mcp`, `pymupdf`, `pillow` (총 3개)
- 시스템 의존성: 없음
- MCP SDK: FastMCP (`from mcp.server.fastmcp import FastMCP`)

## 책임 분담

| 주체 | 역할 |
|---|---|
| pdf-study MCP (우리) | PDF 처리, 챕터 분리, (OCR 모드) 페이지→이미지 렌더, 학습 자료 렌더링 |
| 메인 LLM (Claude/GPT/Gemini) | 챕터 요약, 4유형 문제 생성, 책 정보 추출, 본문/OCR 오류 자연 교정. **OCR 모드에선 sub-agent가 페이지 이미지를 직접 읽어 본문을 OCR** |
| Exa Web Research MCP (HTTP 내부 흡수) | 확장 문제용 외부 검색 (API key 불필요) |
| serve.py (생성물) | 학습 시 localhost 서버 + 진도 read/write |

## 사용자 노출 정책

- 사용자가 등록하는 MCP: **pdf-study 1개**
- 외부 MCP(Exa)는 우리 MCP가 내부에서 HTTP로 호출 → 사용자에게 비공개
- pdf-mcp 같은 외부 의존성 없음 (PyMuPDF 직접 사용)

## 사용자 셋업

```bash
pip install pdf-study  # 개발 중에는 pip install -e .
```

```json
// claude_desktop_config.json (또는 호환 클라이언트)
{
  "mcpServers": {
    "pdf-study": {
      "command": "python",
      "args": ["-m", "pdf_study"]
    }
  }
}
```
