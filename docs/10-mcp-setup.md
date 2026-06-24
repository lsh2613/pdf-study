# MCP 설치 가이드

이 MCP는 전역 Python이나 작업 중인 다른 프로젝트의 Python에 의존하지 않도록,
`pdf-study` 저장소 안의 프로젝트 로컬 `.venv`로 실행하는 것을 권장한다.

## 권장 설치 흐름

```bash
git clone <repo-url> ~/pdf-study
cd ~/pdf-study
./scripts/setup_mcp.sh
```

`scripts/setup_mcp.sh`는 다음 작업을 수행한다.

- `pdf-study/.venv` 생성
- `.venv` 안에 `pdf-study`와 필수 의존성 설치
- `pymupdf`, `pillow`, `rich`, `markdown-it-py`, `mcp` import 검증
- MCP 클라이언트에 붙여 넣을 JSON 설정 출력

## MCP 설정 템플릿

문서에서 쓰는 일반 형태는 아래와 같다.

```json
{
  "mcpServers": {
    "pdf-study": {
      "command": "<PDF_STUDY_INSTALL_DIR>/.venv/bin/python",
      "args": ["-m", "pdf_study"]
    }
  }
}
```

`<PDF_STUDY_INSTALL_DIR>`는 `pyproject.toml`이 있는 `pdf-study` 저장소의 절대경로다.
MCP 설정의 `command`에는 `~`, `$HOME`, 상대경로, 전역 Python을 쓰지 말고
설치 스크립트가 출력한 절대경로를 그대로 복사한다.

## 출력 예시

설치 위치가 `/Users/alice/pdf-study`라면 스크립트는 이런 설정을 출력한다.

```json
{
  "mcpServers": {
    "pdf-study": {
      "command": "/Users/alice/pdf-study/.venv/bin/python",
      "args": ["-m", "pdf_study"]
    }
  }
}
```

이렇게 등록하면 어느 작업 프로젝트에서 PDF 학습 자료 생성을 요청하더라도
MCP 서버는 항상 `pdf-study` 전용 `.venv`로 실행된다. 다른 프로젝트의 `.venv`나
시스템 Python에 패키지를 설치할 필요가 없다.

## 확인 명령

이미 설치된 환경을 확인하려면:

```bash
./scripts/setup_mcp.sh --check
```

MCP 설정 JSON만 다시 출력하려면:

```bash
./scripts/setup_mcp.sh --print-config
```

## 피해야 할 설정

아래처럼 전역 Python을 직접 가리키면 사용자 환경마다 의존성 문제가 생길 수 있다.

```json
{
  "mcpServers": {
    "pdf-study": {
      "command": "python",
      "args": ["-m", "pdf_study"]
    }
  }
}
```

`~/pdf-study/.venv/bin/python`도 MCP 클라이언트가 `~`를 확장하지 않을 수 있으므로
설정 파일에는 쓰지 않는다. `/pdf-study/.venv/bin/python`은 루트 디렉터리 아래 경로라
대부분의 사용자 환경에서 존재하지 않는다.
