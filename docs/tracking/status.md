# 현재 상태

## 구현됨

- 로컬 MCP 서버가 `init_work`부터 `finalize_study`까지 PDF 학습 자료 생성 흐름을 제공한다.
- 새 `init_work`가 단답형·주관식·확장형 생성 여부를 차례로 요청한 뒤 필수 학습자
  정보를 항상 요청하고, 모두 승인·검증한 뒤 작업을 만든다. 객관식은 기존 호환을
  위해 기본 활성이다. 기존 작업의 미확정 설정을 보완하는 `scan_pdf`는 선택적
  학습자 정보 동작을 유지한다.
- PDF 내장 목차가 있으면 `pdf_pages` 기준 챕터 후보와 선택적 `source_pages` 원문 번호를 만들고, 없으면 `scan_pdf`가 목차 페이지 이미지를 렌더한다. OCR 모델 준비는 `prepare_ocr`, 목차 이미지 OCR은 `scan_toc_with_ocr`가 담당한다.
- 텍스트 레이어 품질을 평가해 텍스트 없음과 모지바케를 구분하고, 신뢰할 수 없는 text 모드를 거부한다. OCR이 필요하면 한국어·영어 중 하나를 명시적으로 선택해 해당 모델을 작업 상태에 보존한다.
- text 모드는 챕터 본문을 서버가 추출하고, OCR 모드는 `set_chapters` 시점에 PaddleOCR CPU로 본문을 선계산해 raw에 저장한다. raw `text`와 `char_count`가 누락되거나 불일치하면 sub-agent 프롬프트와 챕터 본문 반환을 거부한다.
- 챕터별 요약, 기본 문제, 확장 문제를 분리 JSON으로 저장하고, 현재 프롬프트의 JSON 양식에 맞지 않는 결과는 완료 상태로 바꾸지 않는다.
- 요약 전에 전체 본문의 실제 제목·순서·계층만 `section_inventory`로 만들고,
  서브 챕터가 없으면 챕터 전체 section 하나만 둔다. inventory로 내용을 선별하지
  않고 각 section의 원문 전체를 직접 요약한다. 원문·inventory·초안을 대조한 검토가
  모든 section과 챕터 전체의 중요 누락·왜곡 부재를 확인한 경우에만 완료로 저장하며
  고정 글자 수 기준은 쓰지 않는다. 저장 경계는 raw에서 확실히 식별되는 번호형 계층도
  inventory와 직접 대조해 거짓 `passed`를 거부한다.
- 분리 workflow의 학습자 정보는 inventory·요약·검토에서 분리하고 기본·확장 문제의
  난이도·표현·예시·관점 조정에만 사용한다. 출력에는 `학습용 요약`임을 명시한다.
- 기본·확장 문제는 검토를 통과한 저장 요약만 근거로 생성한다. 확장 문제는 외부 검색을 사용하지 않으며 서버에는 외부 검색 도구나 검색용 HTTP 클라이언트가 없다.
- 잘못된 작업 ID, 등록되지 않은 챕터, 건너뛰기 챕터, 상태 저장 실패는 요약·퀴즈·확장 JSON 파일을 새로 남기지 않으며, 기존 파일이 있으면 실패 전 내용으로 되돌린다.
- 병렬 챕터 저장을 고려해 작업 상태 갱신은 잠금과 원자적 파일 교체를 사용한다.
- `set_chapters`는 입력과 책 정보 준비를 무부작용으로 검증한 뒤 모드·챕터·처리 phase를 한 번에 확정하며, 같은 작업의 호출을 직렬화하고 본문 준비 성공과 실패를 `chapter_processing`에 종결 상태로 남긴다.
- HTML 사이트와 Markdown+TUI 출력이 같은 저장 결과에서 생성된다.
- Markdown+TUI는 진행 중인 챕터를 다시 열면 저장된 답안을 건너뛰고 첫 미응답 문제부터 자동 재개한다.
- HTML 결과는 macOS/Linux의 `start_study.sh`와 Windows의 `start_study.bat`을 함께 제공한다. 같은 컴퓨터의 프로젝트 환경에서 더블클릭하면 사용 가능한 loopback 포트를 자동 선택해 브라우저를 열며, 기존 `study_html.py` 직접 실행 정보도 호환을 위해 유지한다.
- 기존 출력 작업은 `init_work`가 이어가기·교체 선택을 요구하며 자동 덮어쓰지 않는다. 렌더 결과는 `.pdf-learner-manifest.json`의 관리 경로만 staging 세대로 교체하고, 같은 형식·학습 fingerprint에서만 진도를 유지한다.
- 서버 재시작 후 `resume_work`로 기존 `.work` 상태를 다시 등록할 수 있다.
- 프로젝트 로컬 `.venv` 설치 스크립트와 전역 MCP 클라이언트 설정 자동 적용, 환경
  확인 명령이 있다. 기존 venv는 재사용하며 MCP Python SDK는 FastMCP v1 호환 범위인
  `>=1.28,<2`로 제한한다. 기본 설치는 런타임만 준비하고 `setup_mcp.sh --dev`는
  pytest까지 준비·검증한다. Codex CLI는 공식 `codex mcp add` 뒤 조회로 등록을
  확인한다.
  Codex의 전역 `never` 승인 정책은 원본 설정을 백업한 뒤 모든 granular 승인 범주가
  `true`인 정책으로 변환한다. 과거 설치 흐름이 만든 알려진 여러 줄 TOML 정책은 같은
  정책으로 복구하며, 다른 TOML 오류는 수정하지 않는다.
- 배포 wheel은 서버·렌더러·템플릿만 포함하고 개발용 `pdf_learner.tests*` 패키지는 포함하지 않는다. 저장소에서의 pytest와 fixture 생성은 소스 트리를 사용한다.
- HTML과 Markdown+TUI 렌더러 테스트는 `tests/conftest.py`의 공용 작업 준비 helper를 사용해 같은 MCP 저장·렌더 흐름을 검증한다.
- 처리 모드 선택지는 `processing_mode_contract.py`가 Elicitation용 독립
  text/OCR·sequential/parallel 선택지와 텍스트 품질별 OCR 제한만 관리한다.
- HTML·Markdown+TUI·출력 fingerprint는 `renderer/study_loader.py`의 중립 렌더 입력을 공유하므로, 완료 결과만 읽기와 skip 챕터 제외 규칙이 출력 형식별로 갈라지지 않는다.
- Markdown+TUI 렌더러의 설계 주석은 현재 `docs/contracts.md`와 `docs/architecture.md`를 참조하며, 삭제된 문서 경로를 가리키지 않는다.
- 사용자 선택 파라미터와 구조화 fallback은 공개 MCP 계약에서 제거했다. 선택이
  필요한 일곱 도구는 MCP form Elicitation 미지원 세션에서 fail-closed한다.
- 모든 정적·동적 Elicitation form은 Codex primitive schema 부분집합을 사용한다.
  새 작업의 학습자 정보는 `required`, `minLength=1`, default 없음인 `string`으로
  표현하고 공백뿐인 값도 작업 생성 전에 거부한다. 기존 작업 보완용 선택 입력은 빈
  문자열 기본값을 유지한다. Pydantic이 만드는 최상위 모델 `title`과 nullable
  `anyOf`는 wire schema에서 제거한다.
- Elicitation 필드 제목은 한국어로 표시하고, 필요한 선택지 설명만 같은 enum 항목에
  넣는다. `message`에는 단계 고유 정보만 남기며, 표시용 `md+tui`, `순차 처리`,
  `병렬 처리` 등은 승인 뒤 기존 내부값으로 변환한다. 새 작업은 선택형 문제가 모두
  꺼져 있어도 학습자 정보 form을 네 번째로 연다. 문제 유형은 Codex의 다중 필드 탐색
  순서에 의존하지 않도록 단일 필드 form으로 단답형·주관식·확장형 순서대로 요청한다.
- 선택이 필요한 일곱 워크플로는 Elicitation과 승인 후 실행을 결합한 등록 async
  함수 하나씩만 유지한다. 같은 작업을 선택 인자로 직접 실행하는 `_impl` 함수나
  별도 MCP wrapper는 제거했다.
- 공개 도구 설명, 오류와 `next_action`은 등록된 MCP 입력만 호출 예시에 사용하며,
  제거된 선택 인자나 번호형 자유 텍스트 fallback을 다시 안내하지 않는다.
- 새 작업의 고정 출력 경로 안내와 문제 유형·필수 학습자 정보, OCR 언어, 챕터
  구성·범위, 본문 추출 방식, 실행 방식, 최종 형식, 기존 작업 재개·교체와 `.work`
  정리를 실행 직전에 서버가 직접 확인한다. `set_chapters`의 기본 선택은 독립
  form으로 순서대로 받고, 직접 입력·균등 청크의 조건부 입력까지 모두 승인된 뒤에만
  상태 변경을 시작한다.
- `init_work(pdf_path)`와 `resume_work(pdf_path)`는 MCP 서버 프로젝트 루트 아래
  `result/<pdf-name>`을 고정 경로로 사용한다. 공개 `output_dir`은 없고 요청
  workspace, MCP root, 프로세스 cwd에 따라 경로가 달라지지 않는다.
- 입력 없는 `list_study_results()`는 고정 result 루트의 PDF별 직접 하위
  디렉터리를 정렬된 절대 경로로 반환하고 조회 중 상태를 변경하지 않는다.
- 완료된 렌더 결과는 `cleanup_work`로 렌더링을 다시 하지 않고 `.work` 중간 데이터만 제거할 수 있다. 미완료 작업은 재개 데이터를 보호하기 위해 거부한다.
- `finalize_study`는 `force` 없이도 완료분 자료를 만들며, 미반영 챕터의 결과 유형·상태·오류는 성공 응답의 `omitted_chapters`와 실행 안내로 알린다.

## 검증 상태

- 테스트 모음은 PDF 스캔, 챕터 경계 추천, OCR 선계산 입력, raw 본문 저장, 서버 응답 봉투, Elicitation 강제와 공개 스키마, 최종 렌더링, 진도 저장 서버, 설치 스크립트와 MCP 설정 보호를 다룬다.
- 테스트 시작 시 fixture 생성기 fingerprint와 PDF 해시를 확인해 오래된 합성 PDF를 자동 재생성한다.
- 최근 확인: 현재 checkout의 프로젝트 `.venv`에서
  `.venv/bin/python -m pytest -q`로 387개 테스트가 모두 통과했다. 경고는
  PyMuPDF/Paddle 하위 SWIG 타입의 DeprecationWarning 5개다.

## 남은 일

- 실제 대형 스캔본에서 OCR 모드 CPU 사용량과 처리 시간을 줄이는 배치 전략을 더 정교하게 만들 수 있다.
- 결과물 실행 안내는 HTML과 TUI 각각에 있지만, MCP 클라이언트별 설정 예시는 현재 로컬 venv 중심 안내에 머문다.
- 챕터별 실패 재시도 정책은 문서와 next_action으로 안내하지만, 실패한 챕터를 자동으로 다시 큐잉하는 별도 도구는 없다.
- OCR 입력은 현재 한국어와 영어만 지원한다. 학습 자료의 출력 언어와는 별개다.

## 막힌 일

현재 작업을 막는 외부 의존성이나 미해결 결정은 없다.
