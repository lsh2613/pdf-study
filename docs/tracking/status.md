# 현재 상태

## 구현됨

- 로컬 MCP 서버가 `init_work`부터 `finalize_study`까지 PDF 학습 자료 생성 흐름을 제공한다.
- `init_work`가 단답형·주관식·확장형 생성 여부와 선택적 학습자 정보를 요청하고, `scan_pdf`가 사용자의 명시적 선택을 확정한 뒤 스캔한다. 객관식만 기존 호환을 위해 기본 활성이다.
- PDF 내장 목차가 있으면 `pdf_pages` 기준 챕터 후보와 선택적 `source_pages` 원문 번호를 만들고, 없으면 `scan_pdf`가 목차 페이지 이미지를 렌더한다. OCR 모델 준비는 `prepare_ocr`, 목차 이미지 OCR은 `scan_toc_with_ocr`가 담당한다.
- 텍스트 레이어 품질을 평가해 텍스트 없음과 모지바케를 구분하고, 신뢰할 수 없는 text 모드를 거부한다. OCR이 필요하면 한국어·영어 중 하나를 명시적으로 선택해 해당 모델을 작업 상태에 보존한다.
- text 모드는 챕터 본문을 서버가 추출하고, OCR 모드는 `set_chapters` 시점에 PaddleOCR CPU로 본문을 선계산해 raw에 저장한다. raw `text`와 `char_count`가 누락되거나 불일치하면 sub-agent 프롬프트와 챕터 본문 반환을 거부한다.
- 챕터별 요약, 기본 문제, 확장 문제를 분리 JSON으로 저장하고, 현재 프롬프트의 JSON 양식에 맞지 않는 결과는 완료 상태로 바꾸지 않는다.
- 확장 문제는 외부 검색 없이 같은 챕터 본문과 학습자 정보만으로 생성한다. 서버에는 외부 검색 도구나 검색용 HTTP 클라이언트가 없다.
- 잘못된 작업 ID, 등록되지 않은 챕터, 건너뛰기 챕터, 상태 저장 실패는 요약·퀴즈·확장 JSON 파일을 새로 남기지 않으며, 기존 파일이 있으면 실패 전 내용으로 되돌린다.
- 병렬 챕터 저장을 고려해 작업 상태 갱신은 잠금과 원자적 파일 교체를 사용한다.
- `set_chapters`는 입력과 책 정보 준비를 무부작용으로 검증한 뒤 모드·챕터·처리 phase를 한 번에 확정하며, 같은 작업의 호출을 직렬화하고 본문 준비 성공과 실패를 `chapter_processing`에 종결 상태로 남긴다.
- HTML 사이트와 Markdown+TUI 출력이 같은 저장 결과에서 생성된다.
- Markdown+TUI는 진행 중인 챕터를 다시 열면 저장된 답안을 건너뛰고 첫 미응답 문제부터 자동 재개한다.
- HTML 결과는 macOS/Linux의 `start_study.sh`와 Windows의 `start_study.bat`을 함께 제공한다. 같은 컴퓨터의 프로젝트 환경에서 더블클릭하면 사용 가능한 loopback 포트를 자동 선택해 브라우저를 열며, 기존 `study_html.py` 직접 실행 정보도 호환을 위해 유지한다.
- 기존 출력 작업은 `init_work`가 이어가기·교체·새 폴더 선택을 요구하며 자동 덮어쓰지 않는다. 렌더 결과는 `.pdf-study-manifest.json`의 관리 경로만 staging 세대로 교체하고, 같은 형식·학습 fingerprint에서만 진도를 유지한다.
- 서버 재시작 후 `resume_work`로 기존 `.work` 상태를 다시 등록할 수 있다.
- 프로젝트 로컬 `.venv` 설치 스크립트와 전역 MCP 클라이언트 설정 자동 적용, 환경 확인 명령이 있다. 기본 설치는 런타임만 준비하고 `setup_mcp.sh --dev`는 pytest까지 준비·검증한다. Claude Code·Antigravity CLI의 손상된 기존 JSON 설정은 백업·덮어쓰지 않으며, Codex CLI는 공식 `codex mcp add` 뒤 조회로 등록을 확인한다.
- 배포 wheel은 서버·렌더러·템플릿만 포함하고 개발용 `pdf_study.tests*` 패키지는 포함하지 않는다. 저장소에서의 pytest와 fixture 생성은 소스 트리를 사용한다.
- HTML과 Markdown+TUI 렌더러 테스트는 `tests/conftest.py`의 공용 작업 준비 helper를 사용해 같은 MCP 저장·렌더 흐름을 검증한다.
- 처리 모드 선택지는 `processing_mode_contract.py`가 호환용 순차/병렬·text/OCR 네 조합과 elicitation용 독립 추출·실행 선택지, 텍스트 품질별 OCR 제한, 다음 단계와 fallback 응답을 함께 관리한다. 기본값이나 추천으로 오해할 표현은 붙이지 않는다.
- HTML·Markdown+TUI·출력 fingerprint는 `renderer/study_loader.py`의 중립 렌더 입력을 공유하므로, 완료 결과만 읽기와 skip 챕터 제외 규칙이 출력 형식별로 갈라지지 않는다.
- Markdown+TUI 렌더러의 설계 주석은 현재 `docs/contracts.md`와 `docs/architecture.md`를 참조하며, 삭제된 문서 경로를 가리키지 않는다.
- 다음 도구에 사용자 선택이 필요하면 앞선 성공 응답의 `next_step`이 필수 파라미터와 구조화된 선택지를 제공한다. 처리 모드와 출력 형식의 실패 응답 선택지는 잘못된 호출을 위한 fallback으로 유지한다.
- MCP form elicitation 지원 세션에서는 새 작업의 절대 출력 폴더와 문제 유형, OCR 언어, 챕터 구성·범위, 본문 추출 방식, 실행 방식, 최종 형식, 기존 작업 재개·교체와 `.work` 정리를 실행 직전에 서버가 직접 확인하고 응답값을 호출 인자보다 우선한다. `set_chapters`의 세 선택은 독립 form으로 순서대로 받고 모두 승인된 뒤에만 상태 변경을 시작한다.
- 빈 또는 상대 `output_dir`은 요청의 단일 agent workspace를 기준으로 계산하며, workspace가 없거나 모호하면 서버 cwd로 폴백하지 않고 절대 경로를 요구한다.
- 완료된 렌더 결과는 `cleanup_work`로 렌더링을 다시 하지 않고 `.work` 중간 데이터만 제거할 수 있다. 미완료 작업은 재개 데이터를 보호하기 위해 거부한다.
- `finalize_study`는 `force` 없이도 완료분 자료를 만들며, 미반영 챕터의 결과 유형·상태·오류는 성공 응답의 `omitted_chapters`와 실행 안내로 알린다.

## 검증 상태

- 테스트 모음은 PDF 스캔, 챕터 경계 추천, OCR 선계산 입력, raw 본문 저장, 서버 응답 봉투, 선택지 요구, 최종 렌더링, 진도 저장 서버, 설치 스크립트와 MCP 설정 보호를 다룬다.
- 테스트 시작 시 fixture 생성기 fingerprint와 PDF 해시를 확인해 오래된 합성 PDF를 자동 재생성한다.
- 최근 확인: 현재 checkout에서 `.venv/bin/python -m pytest -q`가 325개 테스트를 모두 통과했다. 경고는 PyMuPDF/Paddle 하위 SWIG 타입의 DeprecationWarning 5개다.

## 남은 일

- 실제 대형 스캔본에서 OCR 모드 CPU 사용량과 처리 시간을 줄이는 배치 전략을 더 정교하게 만들 수 있다.
- 결과물 실행 안내는 HTML과 TUI 각각에 있지만, MCP 클라이언트별 설정 예시는 현재 로컬 venv 중심 안내에 머문다.
- 챕터별 실패 재시도 정책은 문서와 next_action으로 안내하지만, 실패한 챕터를 자동으로 다시 큐잉하는 별도 도구는 없다.
- OCR 입력은 현재 한국어와 영어만 지원한다. 학습 자료의 출력 언어와는 별개다.

## 막힌 일

현재 작업을 막는 외부 의존성이나 미해결 결정은 없다.
