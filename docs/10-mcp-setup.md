# MCP 로컬 venv 설치

pdf-study MCP는 전역 Python이 아니라 저장소 안의 프로젝트 로컬 `.venv`로 실행한다. MCP 클라이언트 설정의 `command`에는 `<PDF_STUDY_INSTALL_DIR>/.venv/bin/python`처럼 이 저장소의 절대 경로가 등록되어야 한다.

```bash
scripts/setup_mcp.sh
```

이 명령은 `.venv` 생성, 패키지 설치, import 검증을 실행한 뒤 Claude Code, Codex CLI, Antigravity CLI 설정을 자동 적용한다. 대상 클라이언트를 지정하지 않으면 세 클라이언트 설정을 모두 적용한다.

기본 설치는 MCP 실행에 필요한 런타임 의존성만 설치한다. 테스트까지 실행할 개발 환경이 필요하면 다음처럼 `--dev`를 붙인다.

```bash
scripts/setup_mcp.sh --dev
```

`--dev`는 기본 런타임 의존성에 pytest를 추가로 설치하고 검증한다. 일반 사용자는 기본 설치만으로 PDF 학습 MCP를 바로 실행할 수 있다.

특정 클라이언트만 갱신하려면 대상 옵션을 붙인다.

```bash
scripts/setup_mcp.sh --claude
scripts/setup_mcp.sh --codex
scripts/setup_mcp.sh --antigravity-cli
```

기본 설정 범위는 현재 프로젝트의 로컬 설정이다. 전역 설정에 적용하려면 `--global`을 붙이고, 로컬 설정을 명시하려면 `--local`을 붙인다.

설정 JSON만 다시 보고 싶으면 다음 명령을 쓴다.

```bash
scripts/setup_mcp.sh --print-config
```

`--print-config`는 설치, import 검증, 클라이언트 설정 적용을 하지 않고 현재 checkout 기준 MCP 설정 JSON만 출력한다.

이미 설치된 `.venv`가 정상인지 확인하려면 다음 명령을 쓴다.

```bash
scripts/setup_mcp.sh --check
```

`--check`는 기존 `.venv`에서 필수 import가 가능한지만 확인한다. `.venv` 생성, 패키지 설치, 클라이언트 설정 적용은 하지 않는다.

`uv`가 없으면 스크립트가 자동 설치를 시도할 수 있다. macOS에서는 PaddleOCR 실행에 필요한 `libomp`가 없고 Homebrew가 있으면 `brew install libomp`를 시도할 수 있다.

설정의 `command` 값은 `python`, `python3`, `~/...` 같은 값으로 바꾸지 않는다. 절대 경로를 유지해야 MCP 클라이언트가 같은 의존성 환경에서 `python -m pdf_study`를 실행한다.
