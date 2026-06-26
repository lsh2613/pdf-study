# MCP 로컬 venv 설치

pdf-study MCP는 전역 Python이 아니라 저장소 안의 프로젝트 로컬 `.venv`로 실행한다. MCP 클라이언트 설정에는 `<PDF_STUDY_INSTALL_DIR>/.venv/bin/python`처럼 이 저장소의 절대 경로를 넣는다.

```bash
scripts/setup_mcp.sh
```

이 명령은 `.venv` 생성, 패키지 설치, import 검증을 실행한 뒤 MCP 클라이언트 설정에 복사할 JSON을 출력한다.

설정 JSON만 다시 보고 싶으면 다음 명령을 쓴다.

```bash
scripts/setup_mcp.sh --print-config
```

이미 설치된 `.venv`가 정상인지 확인하려면 다음 명령을 쓴다.

```bash
scripts/setup_mcp.sh --check
```

출력된 `command` 값은 `python`, `python3`, `~/...` 같은 값으로 바꾸지 않는다. 절대 경로를 그대로 복사해야 MCP 클라이언트가 같은 의존성 환경에서 `python -m pdf_study`를 실행한다.
