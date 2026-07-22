# 실행 절차

## 준비 조건

- Python 3.10 이상이 필요하다.
- 저장소 루트에서 명령을 실행해야 한다. 이 프로젝트는 루트 디렉터리 자체를 `pdf_study` 패키지로 매핑한다.
- MCP 클라이언트에는 저장소 안 `.venv/bin/python`의 절대 경로를 등록해야 한다.

## 설치

```bash
./scripts/setup_mcp.sh
```

이 명령은 저장소 안에 `.venv`를 만들고, 패키지를 editable로 설치하고, `mcp`, `fitz`, `PIL`, `rich`, `markdown_it`, `paddle`, `paddleocr`, `pdf_study` import를 확인한다. 검증이 끝나면 Claude Code, Codex CLI, Antigravity CLI 설정을 저장소 루트 기준으로 자동 적용한다. 저장소 밖에서 스크립트를 호출해도 로컬 설정은 저장소에 기록된다. 대상 클라이언트를 지정하지 않으면 세 클라이언트 설정을 모두 적용한다.

기존 MCP 설정이 손상된 JSON이거나 예상한 객체 구조가 아니면 기존 파일을 덮어쓰지 않고 설치를 중단한다. 기존 설정을 변경할 때는 같은 위치의 `.pdf-study.bak` 백업을 만든 뒤 원자적으로 교체한다.

기본 설치는 MCP 실행에 필요한 런타임 의존성만 설치한다. 테스트까지 실행할 개발 환경이 필요하면 다음 명령을 사용한다.

```bash
./scripts/setup_mcp.sh --dev
```

`--dev`는 런타임 의존성에 pytest를 추가로 설치하고 개발 환경 검증까지 수행한다. 일반 사용자는 기본 설치만으로 별도 Python 패키지 설치 없이 MCP를 실행할 수 있다.

특정 클라이언트만 갱신하려면 `--claude`, `--codex`, `--antigravity-cli` 중 필요한 옵션을 붙인다. 기본은 현재 프로젝트의 로컬 설정이며, 전역 설정에 적용하려면 `--global`을 붙인다.

설정만 다시 출력하려면 다음 명령을 쓴다.

```bash
./scripts/setup_mcp.sh --print-config
```

`--print-config`는 설치나 설정 적용 없이 현재 checkout 기준 JSON만 출력한다.

이미 만든 환경이 정상인지 확인하려면 다음 명령을 쓴다.

```bash
./scripts/setup_mcp.sh --check
```

`--check`는 기존 `.venv`에서 필수 import가 가능한지만 확인한다. `.venv`를 만들거나 패키지를 설치하거나 클라이언트 설정을 적용하지 않는다.

`uv`가 없으면 스크립트가 자동 설치를 시도할 수 있다. macOS에서 `libomp`가 없고 Homebrew가 있으면 PaddleOCR 실행을 위해 `brew install libomp`를 시도할 수 있다.

## 개발 중 실행

MCP 서버 진입점은 다음 명령이다.

```bash
.venv/bin/python -m pdf_study
```

일반 터미널에서 실행하면 stdio MCP 서버로 대기한다. 실제 도구 호출은 MCP 클라이언트가 이 프로세스를 자식 프로세스로 띄워 수행한다.

## OCR 준비 흐름

`scan_pdf`는 PDF 텍스트 품질과 내장 목차 여부를 확인하고, 내장 목차가 없을 때 목차 후보 이미지를 렌더한다. 이 단계에서는 PaddleOCR 모델 다운로드, 모델 로드, OCR 실행을 하지 않는다.

목차 이미지 OCR이나 본문 OCR이 필요하면 먼저 다음 도구로 모델 준비 상태를 드러낸다.

```text
prepare_ocr(work_id)
```

목차 후보 이미지를 읽을 때는 다음 도구를 호출한다.

```text
scan_toc_with_ocr(work_id)
```

모델 캐시가 없으면 `scan_toc_with_ocr`와 `set_chapters(..., extraction_mode="ocr")`는 OCR을 시작하지 않고 `prepare_ocr` 호출을 안내한다. 모델 캐시가 있으면 내부 모델 로드는 허용된다.

## 검증

개발 환경을 아직 만들지 않았다면 먼저 `./scripts/setup_mcp.sh --dev`를 실행한다. 전체 회귀 확인은 다음 명령으로 한다.

```bash
.venv/bin/python -m pytest
```

pytest 시작 시 `tests/fixtures/.fixture-manifest.json`을 확인한다. 합성 PDF가 없거나
fixture 생성기·입력 폰트·PDF 파일 해시가 현재 manifest와 다르면 fixture를 자동으로
재생성한다. manifest와 생성 PDF는 로컬 ignored 파일이므로 커밋하지 않는다.

변경 범위가 좁을 때는 관련 테스트를 먼저 실행하고, 완료 전 전체 테스트를 실행한다. 예시는 다음과 같다.

```bash
.venv/bin/python -m pytest tests/test_pdf_reader.py tests/test_analysis_e2e.py
.venv/bin/python -m pytest tests/test_server.py
.venv/bin/python -m pytest tests/test_renderer.py tests/test_md_tui_renderer.py
.venv/bin/python -m pytest tests/test_setup_mcp.py
```

## 결과물 실행

같은 출력 폴더에 기존 작업이나 완료 결과가 있으면 `init_work`는 자동 덮어쓰지 않는다. 응답의 `resume`, `replace`, `new_output_dir` 선택지를 그대로 보여주고 사용자의 답에 따라 `resume_work`, `init_work(..., replace_existing=true)`, 또는 다른 `output_dir`의 `init_work`를 호출한다. `replace`는 기존 `.work`를 새 작업으로 바꾸지만, 이전 렌더 결과는 새 렌더가 성공할 때까지 유지한다.

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

결과 폴더의 `.pdf-study-manifest.json`은 현재 형식과 서버가 생성한 경로를 기록한다. 같은 작업을 다시 렌더하면 manifest 경로만 새 세대로 교체되며, 형식과 학습 fingerprint가 같을 때만 기존 진도가 유지된다.

최종 결과를 확인한 뒤 PDF 본문·raw·상태가 든 `.work/`만 지우려면 MCP의
`cleanup_work(work_id)`를 호출한다. 이 동작은 결과물·진도·manifest를 유지하고
다시 렌더링하지 않는다. 렌더가 완료되지 않은 작업은 재개 데이터를 보호하기 위해
정리할 수 없다.

## 로컬 산출물

`result/`, `.work/`, `.venv/`, `.pytest_cache/`, 생성 fixture PDF는 커밋 대상이 아니다. `.work/`에는 사용자의 PDF 본문과 학습 결과가 들어갈 수 있으므로 공개 저장소에 포함하면 안 된다.
