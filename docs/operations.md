# 실행 절차

## 준비 조건

- Python 3.10 이상이 필요하다.
- 저장소 루트에서 명령을 실행해야 한다. 이 프로젝트는 루트 디렉터리 자체를 `pdf_study` 패키지로 매핑한다.
- MCP 클라이언트에는 저장소 안 `.venv/bin/python`의 절대 경로를 등록해야 한다.

## 설치

```bash
./scripts/setup_mcp.sh
```

이 명령은 저장소 안에 `.venv`를 만들고, 패키지를 editable로 설치하고, `mcp`, `fitz`, `PIL`, `rich`, `markdown_it`, `pdf_study` import를 확인한다. 마지막에 MCP 클라이언트 설정에 복사할 JSON을 출력한다.

설정만 다시 출력하려면 다음 명령을 쓴다.

```bash
./scripts/setup_mcp.sh --print-config
```

이미 만든 환경이 정상인지 확인하려면 다음 명령을 쓴다.

```bash
./scripts/setup_mcp.sh --check
```

## 개발 중 실행

MCP 서버 진입점은 다음 명령이다.

```bash
.venv/bin/python -m pdf_study
```

일반 터미널에서 실행하면 stdio MCP 서버로 대기한다. 실제 도구 호출은 MCP 클라이언트가 이 프로세스를 자식 프로세스로 띄워 수행한다.

## 검증

전체 회귀 확인은 다음 명령으로 한다.

```bash
.venv/bin/python -m pytest
```

변경 범위가 좁을 때는 관련 테스트를 먼저 실행하고, 완료 전 전체 테스트를 실행한다. 예시는 다음과 같다.

```bash
.venv/bin/python -m pytest tests/test_pdf_reader.py tests/test_analysis_e2e.py
.venv/bin/python -m pytest tests/test_server.py
.venv/bin/python -m pytest tests/test_renderer.py tests/test_md_tui_renderer.py
.venv/bin/python -m pytest tests/test_setup_mcp.py
```

## 결과물 실행

HTML 결과물은 결과 폴더에서 다음 명령으로 연다.

```bash
python3 study_html.py
```

이 서버를 거쳐야 답안과 완료 토글이 `progress/`에 저장된다. HTML 파일을 `file://`로 직접 열면 진도 저장 API가 동작하지 않는다.

Markdown+TUI 결과물은 결과 폴더에서 다음 명령으로 연다.

```bash
python3 study_tui.py
```

`rich`가 없으면 TUI 런처가 설치를 시도하고, 설치할 수 없으면 평문 모드로 동작한다.

## 로컬 산출물

`result/`, `.work/`, `.venv/`, `.pytest_cache/`, 생성 fixture PDF는 커밋 대상이 아니다. `.work/`에는 사용자의 PDF 본문과 학습 결과가 들어갈 수 있으므로 공개 저장소에 포함하면 안 된다.
