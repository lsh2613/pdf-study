# 프로젝트 검토 결과

2026-07-20, `2faeb9f` 기준으로 `docs/` 문서, 프로젝트 지침, MCP 도구 구현,
렌더러, 템플릿, 설치 스크립트, 테스트를 교차 검토했다. 이후 항목별 결정과 해결
결과를 이 문서에 계속 기록한다. 기존 `docs/tracking/findings.md`는 변경하지 않는다.

우선순위는 P0(보안·데이터 경계), P1(결과 정확성·주요 흐름),
P2(유지보수·개발 경험) 순이다.

## 문서와 코드의 불일치

### F-001 [해결: 2026-07-20] 외부 검색 계약과 구현의 불일치

- 결정: 확장 문제는 외부 검색이 없어도 챕터 본문과 학습자 정보로 만들 수 있으므로
  검색 기능 전체를 제거했다. 원문 검색 허용 범위를 새로 정의하는 대신 서버가 외부
  검색을 수행하지 않는 경계로 단순화했다.
- 코드: `search_extension_context`, `exa_client.py`, 검색 파서 테스트를 제거했다.
  확장 결과의 검색 전용 `context`, `sources` 필드와 HTML/TUI 표시 코드도 제거했다.
  확장 결과는 `id`, `question`, `model_answer`만 저장한다.
- 흐름: 객관식만 기존 호환을 위해 기본 활성으로 유지한다. 단답형·주관식·확장형은
  `init_work` 응답의 구조화된 선택지로 사용자에게 생성 여부를 묻고, 답을 받은
  `scan_pdf`가 선택을 잠금 보호 상태에 확정한 뒤 스캔한다. 선택이 빠지면 스캔하지
  않는다.
- 개인화: `init_work`는 학습 목적, 배경지식, 관심 분야, 현재 수준을 선택적으로
  입력하도록 안내하고 `scan_pdf`가 받은 정보를 프롬프트용 상태에 저장한다.
- 문서: 보안·계약·업무 규칙·아키텍처와 결정 기록을 외부 검색 없는 흐름에 맞췄다.

### F-002 [해결: 2026-07-21] PDF·원문 페이지 메타데이터를 끝까지 보존한다

- 결정: 추출 범위의 canonical 키를 `pdf_pages`, 원문에 표시된 페이지 메타를
  `source_pages`로 바꿨다. `source_pages`는 배열뿐 아니라 명시적 `null`도 보존하며
  PDF 추출 범위를 바꾸지 않는다.
- 코드: 추천 결과, 상태, text/OCR raw, OCR 캐시 재사용, `set_chapters` 성공 응답이
  두 canonical 키를 사용한다. 기존 작업과 구형 호출의 `page_range`·
  `printed_range`는 읽을 때만 새 키로 정규화하고 새 저장·응답에서는 제거한다.
- 렌더: HTML 목차·챕터와 Markdown+TUI 책 목차·챕터 요약이 공통 포맷터를 사용해
  `PDF p.N–M · 원문 p.A–B`로 표시한다. 명시적 `source_pages=null`은 오프셋을
  모르면 `원문 페이지 미상`, 알면 `원문 페이지 없음`으로 구분한다.
- 문서: 계약·업무 규칙·아키텍처·PDF 기준과 프로젝트 진입 지침을 새 명칭과
  레거시 호환 경계에 맞췄다.
- 검증: canonical/레거시 입력, 명시적 `null`, text/OCR raw, HTML/TUI 공통 표기를
  포함해 전체 테스트 `236 passed`를 확인했다.

### F-003 [P1] `set_chapters`가 입력 검증을 끝내기 전에 상태를 변경한다 [해결: 2026-07-21]

- 조사 보정: 빈 `chapters`와 잘못된 모드는 원래도 쓰기 전에 거부됐다. 실제 상태
  오염은 `scan_pdf` 전 호출, 필수 필드·범위 오류, 중복 ID에서 모드가 먼저 저장되고,
  검증된 챕터 뒤 책 정보 준비나 본문 처리가 실패할 때 기존 챕터가 이미 교체되는
  경로였다.
- 선택: 입력과 책 정보 fallback을 무부작용으로 준비한 뒤, 검증된 setup과 이후 본문
  처리 결과를 분리하는 방식을 채택했다. 본문 처리까지 전부 성공해야 교체하는 전체
  트랜잭션 방식은 OCR 실패 진단과 성공 raw 재사용 계약을 약화하므로 적용하지 않았다.
- 구현: `analysis.set_chapters_impl`이 스캔 여부, 모드, 챕터 정의·범위·중복과 책 정보를
  먼저 준비한다. `workspace.commit_chapter_setup`은 책 정보와 모드·챕터·phase를 하나의
  잠금 구간에서 확정하고 state 저장 실패 시 책 정보를 복원한다. 복원도 실패하면
  transaction 오류로 드러내며, 같은 작업의 setup과 본문 준비 전체를 직렬화한다.
- 처리 결과: 유효한 setup 이후 본문 준비가 성공하면 `chapter_processing=completed`,
  OCR·추출 실패나 예기치 않은 처리 예외는 새 setup을 유지한 채 `failed`로 남긴다.
  OCR의 `failed_chapters`와 partial raw 금지 계약은 그대로 유지한다.
- 검증: 스캔 전·범위 밖·중복 ID의 상태·책 정보 불변, 기존 완료 상태 보존, setup 단일
  저장, 책 정보 rollback과 rollback 실패, 같은 작업의 동시 setup 직렬화, text 성공,
  OCR 실패와 예기치 않은 예외의 phase를 확인했다.

### F-004 [해결: 2026-07-21] 처리 모드 선택지에 “기본” 표현이 들어가 무기본값 규칙과 충돌한다

- 결정: 네 가지 처리 모드의 실제 선택값, label, 순서와 장단점 설명은 유지하고
  `Sequential + Text`에 붙은 `무난한 기본`·`무난한 기본값` 표현만 제거했다.
- 구현: `server.set_chapters`의 함수 설명, 구조화된 `data.choices` 설명과 모드 미지정
  오류 안내를 같은 의미로 정리했다. 이제 첫 선택지는 `디지털 PDF · 안정적·빠르고
  저렴`으로 표시되며 자동 추천처럼 보이는 표현이 없다.
- 계약: 네 조합 모두 유효하고 기본값이 없다는 기존 동작, OCR 강제 시 OCR 두 조합만
  제시하는 동작은 변경하지 않았다.
- 검증: 모드 미지정 오류의 네 선택지 설명과 금지 표현 부재를 회귀 테스트로 확인했다.

### F-005 [해결: 2026-07-21] 테스트 fixture가 생성기 변경을 감지하지 못한다

- 조사 보정: 과거 clean checkout에서는 `210 passed`, 기존 작업 폴더에서는 stale
  `ko_with_toc.pdf` 때문에 `23 failed, 187 passed`가 나왔다. F-011 이후 기존
  checkout의 테스트 수는 `253개`였고, 이번 회귀 테스트 추가 후 현재는 `256개`다.
- 원인: `tests/conftest.py`가 PDF fixture의 존재 여부만 확인해, 생성기 변경 뒤에도
  기존 ignored PDF를 그대로 사용했다.
- 결정: fixture 생성기와 입력 폰트 fingerprint, 생성된 PDF별 SHA-256을
  `tests/fixtures/.fixture-manifest.json`에 기록한다. manifest가 없거나 fingerprint·
  파일 해시가 다르면 pytest 시작 시 fixture를 재생성하고, 같으면 재사용한다.
- 구현: `build_fixtures.ensure_fixtures`가 manifest 검증과 재생성을 담당하며,
  manifest는 임시 파일 작성 후 교체한다. `tests/conftest.py`는 기존 파일 존재 검사
  대신 이 헬퍼를 호출한다. manifest와 생성 PDF는 계속 git에서 제외한다.
- 검증: stale fingerprint·변경된 PDF는 재생성을 수행하고 최신 manifest는 재생성을
  건너뛰는 테스트를 추가했다. `.venv/bin/pytest -q` 결과는 `256 passed, 5 warnings`
  이다.

### F-006 [해결: 2026-07-21] 개발 의존성 설치와 운영 설치가 분리되지 않았다

- 결정: 일반 사용자는 기본 설치만으로 MCP를 실행하고, 개발·검증 환경은
  `setup_mcp.sh --dev` 한 번으로 준비하도록 한다. 개발 의존성의 canonical 원본은
  `[project.optional-dependencies].dev`로 둔다.
- 구현: `pyproject.toml`의 중복 `[dependency-groups].dev`를 제거하고 pytest를
  `>=9.1.1` 하나로 통일했으며, 사용하지 않는 `pytest-mock`을 제거했다.
  `setup_mcp.sh --dev`는 uv와 pip fallback 모두 `.[dev]`를 설치하고 pytest까지
  검증한다. 기본 설치 경로는 런타임 의존성만 설치한다.
- 문서: README, MCP 설치 안내, 운영 절차에 기본 설치와 `--dev`의 차이를 기록했다.
  `uv.lock`도 단일 dev 의존성 정의에 맞춰 갱신했다.
- 검증: `tests/test_setup_mcp.py`에서 `--dev --check`, 단일 dev 정의와 미사용
  플러그인 제거를 검증했다. `--dev --check`는 런타임·pytest 확인에 성공했고,
  빈 venv의 `--check`는 예상대로 실패했다. 전체 테스트는 `258 passed, 5 warnings`로
  통과했다.

### F-007 [해결: 2026-07-21] 로컬 MCP 설정 적용 위치와 보호 방식이 프로젝트 기준에 맞지 않는다

- 결정: 로컬 설정의 기준을 실행 시점의 `$PWD`가 아닌 스크립트가 계산한 저장소
  루트 `REPO_DIR`로 고정했다. 따라서 저장소 밖에서 스크립트를 호출해도 저장소의
  `.claude.json`, `.codex/mcp.json`, `.agents/mcp_config.json`에 적용된다. `--global`은
  기존처럼 사용자 홈의 전역 설정을 사용한다.
- 구현: 설정 적용 로직을 `scripts/apply_mcp_config.py`로 분리했다. 적용 대상 전체를
  먼저 읽고 JSON 최상위 객체와 MCP 서버 맵을 검증하므로 하나라도 손상되면 어떤
  설정도 변경하지 않고 중단한다.
- 보호: 기존 설정은 `.pdf-study.bak`으로 백업한 뒤 임시 파일 작성·fsync·원자적 교체를
  수행한다. 여러 대상 중 저장이 실패하면 이미 바뀐 설정을 원래 내용으로 복원한다.
- 기본 대상 세 클라이언트 적용 동작은 `scripts/AGENTS.md`와 기존 설치 계약에 맞춰
  유지했다. 특정 클라이언트만 적용하려면 기존처럼 대상 옵션을 사용한다.
- `.gitignore`에 실제 로컬 설정 경로와 `*.pdf-study.bak`을 추가해 사용자별 절대
  경로가 커밋되지 않도록 했다.
- 검증: 저장소 밖 호출 위치, 손상 JSON 무변경 실패, 기존 설정 백업·원자 교체,
  로컬 설정 ignore 경로를 `tests/test_mcp_config.py`로 확인했다.

### F-008 [해결: 2026-07-21] 코드 주석이 존재하지 않는 문서를 가리킨다

- 결정: 삭제된 `docs/05-data-schemas.md`, `docs/07-study-ui.md`를 복원하지 않고,
  현재 canonical 문서인 `docs/contracts.md`, `docs/architecture.md`를 주석의
  설계 기준으로 참조한다.
- 구현: `renderer/md_tui_renderer.py` 모듈 주석의 문서 경로를 현존 문서로 교체했다.
  렌더링 로직과 출력 계약은 변경하지 않았다.
- 검증: 당시 오래된 두 경로가 다시 주석에 들어오지 않고 현존 문서 두 개를
  참조하는지 확인했다. 이후 사용자 결정에 따라 문서 문자열 자체를 검증하는
  테스트는 유지하지 않는다.

## 사용자 흐름의 비효율과 개선점

### F-009 [해결: 2026-07-21] 같은 출력 폴더 재사용 시 이전 작업과 새 작업의 파일이 섞인다

- 결정: 기존 관리 작업을 발견한 `init_work`는 자동 덮어쓰지 않고 재개·교체·새
  출력 폴더 선택지를 반환한다. 교체는 `replace_existing=true`를 명시한 경우에만
  수행하며 새 입력을 검증한 뒤 기존 `.work`만 제거한다.
- 렌더: 새 결과는 staging에서 완성하고 `.pdf-study-manifest.json`에 기록된 이전
  관리 경로만 교체한다. 설치 실패 시 이전 결과와 manifest를 복원하며 manifest 밖의
  사용자 파일과 충돌하면 덮어쓰지 않고 실패한다.
- 진도: 출력 형식과 학습 fingerprint가 모두 같을 때만 HTML/TUI progress를 새
  세대로 복사한다. 형식·챕터·문제 옵션·요약·문제 내용이 달라지면 재사용하지 않는다.
- stale 방지: 현재 상태가 `completed`인 요약·문제 JSON만 렌더 입력으로 읽으므로
  `force=true`도 pending 챕터의 같은 ID 예전 파일을 포함하지 않는다.
- 문서: 계약·업무 규칙·아키텍처·보안·운영·렌더 기준과 결정 기록 0008을 같은
  경계로 갱신했다.

### F-010 [P1] 초 단위 `work_id`가 동시 작업을 구분하지 못한다 [개선 대상 제외: 2026-07-21]

- 발생 조건: 같은 서버 프로세스에서 1초 안에 두 번 `init_work`를 호출한다.
- 관찰 증상: `workspace.make_work_id`는 `YYYYMMDD-HHMMSS`만 사용한다. 두 작업의 ID가
  같으면 `_registry[work_id]`가 마지막 작업 폴더로 덮이고 잠금도 공유된다.
- 영향: 먼저 만든 작업의 후속 호출이 다른 PDF/출력 폴더를 읽고 쓸 수 있다.
- 가능한 접근: UUID/ULID 또는 랜덤 suffix를 사용하고 등록 시 기존 ID가 있으면
  거부한다.
- 처리 결정: 사용자의 명시적 결정에 따라 이번 개선 목록에서는 제외하며 구현을
  변경하지 않는다.

### F-011 [P1] 재개 흐름이 완료된 챕터까지 다시 처리하도록 유도한다 [해결: 2026-07-21]

- 결정: `get_subagent_prompts`가 `summary_pending_chapter_ids`와
  `extension_pending_chapter_ids`를 결과 유형별로 반환하고, 기존 `chapter_ids`는 두
  목록의 자연 정렬 합집합으로 유지하는 호환 방식을 채택했다.
- 처리 경계: 완료 챕터는 `chapter_ids`와 raw 검증 대상에서 제외한다. workflow와
  `get_subagent_prompts`, `get_chapter_content`, 저장 후 응답의 `next_action`은 실제로
  남은 요약 또는 확장 결과만 생성·저장하도록 안내한다.
- 검증: 요약·확장 pending 집합이 서로 다른 재개, pending 자연 정렬과 skip 제외,
  완료 챕터 raw 누락 허용과 pending raw 손상 거부, 확장 비활성·요약 전용·확장 전용·
  전체 완료 안내를 관련 프롬프트·서버·상태 테스트로 확인했다.

### F-012 [해결: 2026-07-22] 정상 선택지를 얻기 위해 일부러 실패 호출을 해야 한다

- 결정: 도구 설명은 고정 계약의 보조 정보로 유지하고, 다음 도구에 사용자 선택이
  필요한 성공 응답은 `data.next_step`에 도구명·필수 파라미터·구조화된 선택지를
  함께 제공한다. 실패 응답의 `data.choices`는 잘못된 호출을 고치는 fallback이다.
- 처리 모드: `scan_pdf`는 항상 이후 `set_chapters`의 처리 모드 조합을
  `set_chapters_next_step`으로 제공한다. 내장 목차 흐름에서는 즉시 `next_step`이고,
  목차 이미지 흐름에서는 OCR 다음에도 `scan_toc_with_ocr.next_step`으로 제공한다.
  text가 불가능하면 OCR 두 조합으로 제한한다.
- 챕터 구성: `recommendations.user_choice_options`를 `{value,label,desc}`의 canonical
  구조로 추가했다. 기존 문자열 `user_choices`는 구형 클라이언트 호환용 value 목록으로
  유지한다.
- 출력 형식: 챕터 설정이 완료되고 두 pending 목록이 비면 `list_pending_chapters`와
  `resume_work`가 `finalize_study`의 `html`·`md_tui` 선택지를 정상 응답의
  `next_step`으로 제공한다.
- 검증: 내장 목차·text 불가 PDF의 처리 모드, OCR 뒤의 처리 모드, 완료된 pending·재개
  작업의 출력 형식, 기존 실패 fallback의 선택지 일치를 회귀 테스트로 확인했다.

### F-013 [해결: 2026-07-22] `.work` 삭제만 원해도 최종 렌더링을 다시 수행한다

- 결정: 결과 생성과 중간 데이터 정리를 분리했다. `finalize_study`가 `.work`를
  보존했으면 성공 응답의 `data.cleanup_work`가 `cleanup_work` 호출을 안내한다.
- `cleanup_work`는 작업 잠금 안에서 rendering 완료 상태를 확인한 뒤 정확히 그
  `.work`만 삭제하고 work_id 등록을 해제한다. 결과·manifest·진도·사용자 파일을
  건드리지 않으며 렌더러를 다시 호출하지 않는다.
- 렌더가 끝나지 않은 작업은 재개 데이터를 보호하기 위해 거부한다. 최초 finalize의
  `keep_work_dir=false`도 같은 안전한 정리 경로를 사용한다.
- 검증: 완료 결과의 HTML·manifest 바이트가 변하지 않고 렌더 함수가 호출되지 않는지,
  미완료 작업의 `.work`가 남는지를 회귀 테스트로 확인했다.

### F-014 [해결: 2026-07-22] 비개발 사용자도 결과를 열기 위해 터미널과 서버를 관리해야 한다

- 결정: HTML 결과에 macOS/Linux용 `start_study.sh`와 Windows용
  `start_study.bat`을 함께 넣어, 사용자가 같은 컴퓨터의 프로젝트 환경에서
  더블클릭으로 시작하게 했다.
- 실행: 런처가 `study_html.py --port 0`을 호출해 사용 가능한 loopback 포트를
  자동 선택하고 브라우저를 연다. 서버 창을 닫거나 그 창에서 `Ctrl+C`를 누르면
  종료한다.
- 호환: `finalize_study`의 기존 `launch_command`, `python`, `entry_page`, 고정
  포트 `default_url`은 유지하고, `launch_scripts`와
  `auto_port_on_script_launch=true`만 HTML 성공 응답에 추가했다. 직접
  `study_html.py --port 8765` 실행과 `progress/` JSON 저장도 그대로다.
- 검증: HTML 완료 응답의 런처 메타데이터와 자동 포트 표시, 런처·정적 서버
  동작을 포함해 전체 테스트 `272 passed, 5 warnings`를 확인했다.

## 불필요하거나 중복 관리되는 파일·정의

### 완료 기록

- [해결: 2026-07-22] `templates/html/grading.js`는 실제 채점 기능 없이 모든 HTML
  결과물에 복사·로드되던 자리표시자였다. 실제 채점과 진도 저장은 `storage.js`가
  계속 담당한다. 파일, 정적 자산 복사 목록, HTML `<script>` 태그와 존재 테스트를
  제거했으며, 추후 기능이 필요하면 기능과 테스트를 함께 추가한다.
- [해결: 2026-07-22] `docs/superpowers/plans/2026-07-20-single-korean-flow.md`는
  바로 다음 구현 커밋 `7e1d682 refactor: 한국어 단일 학습 흐름으로 통합`으로
  적용된 과거 계획이었다. 활성 계획처럼 보이는 미완료 체크박스와 환경 의존적인
  스킬 요구를 남기지 않도록 삭제했으며, 원본과 구현 관계는 Git 이력으로 보존한다.
- [해결: 2026-07-22] `docs/10-mcp-setup.md`와 `docs/operations.md`에 중복되던
  설치·옵션·검증 설명은 `operations.md`를 기준으로 통합했다. 기존 설치 링크와
  북마크를 보존하기 위해 `10-mcp-setup.md`는 기본 설치 명령과 상세 절차 링크만
  제공하는 짧은 진입 문서로 축소했다.
- [해결: 2026-07-22] `AGENTS.md`와 `CLAUDE.md`는 개발용 에이전트 진입 파일로
  둘 다 필요하지만, 본문은 `AGENTS.md` 하나만 관리하도록 `CLAUDE.md → AGENTS.md`
  심볼릭 링크로 통합했다. 문서 내용·링크·주석 문자열을 검증하던 테스트도 사용자
  결정에 따라 제거했다.
- [개선 대상 제외: 2026-07-22] `.claude/skills/commit/SKILL.md`와
  `.codex/skills/commit/SKILL.md`의 공통 절차는 중복되지만, skill 경로와
  `Co-Authored-By` 값은 provider별로 달라야 한다. 사용자의 명시적 결정에 따라
  생성 템플릿이나 중립 skill로 통합하지 않고 두 파일을 유지한다.
- [해결: 2026-07-22] 문제 JSON 계약은 `question_contract.py`를 코드 canonical
  source로 두고, 서버 검증과 프롬프트·fixture 예시가 같은 계약 모듈을 재사용하도록
  통합했다. 외부 JSON 형식과 `data.missing` 경로는 기존 계약을 그대로 유지했다.
- [해결: 2026-07-23] 처리 모드 선택지는 `processing_mode_contract.py`의 단일 내부
  계약으로 통합했다. 네 조합의 공개 선택지, 텍스트 품질별 OCR 제한, `set_chapters`
  다음 단계 객체, 모드 누락 fallback의 `data`와 안내 문구가 같은 정의를 사용한다.
  외부 `choices`, `execution_modes`, `extraction_modes`, OCR 강제 필드와 선택 흐름은
  유지했다.

| 대상 | 관찰 내용 | 정리 방향 |
|---|---|---|
| 렌더 공통 로더 | `MdTuiRenderer`가 중립 데이터 로더를 `html_renderer._load_all`에서 가져온다. | 공통 데이터 로더로 이동해 두 렌더러가 같은 공개 경계를 사용하게 한다. |
| dev 의존성과 `pytest-mock` | optional extra와 dependency group에 pytest가 중복되고 `pytest-mock` 사용처가 없다. | dev 그룹 하나로 통합하고 미사용 의존성을 제거한 뒤 lock을 갱신한다. |
| 배포 패키지의 `pdf_study.tests*` | `pyproject.toml`이 `tests`와 `tests.fixtures`를 런타임 패키지 목록에 명시한다. | 배포물에서 테스트 패키지를 제외하거나 별도 optional 진단 패키지로 목적을 명시한다. |
| 렌더러 테스트 helper | `tests/test_renderer.py`와 `tests/test_md_tui_renderer.py`가 fake summary와 전체 작업 생성 루프를 각각 유지한다. | `conftest.py`의 공용 fixture/helper로 합친다. |

## 권장 처리 순서

1. 완료 — F-001 외부 검색 기능과 불일치를 제거했다.
2. 완료 — F-009에서 출력 폴더와 렌더 세대의 데이터 혼재를 막았다.
3. 제외 — F-010은 개선 대상에서 제외되어 변경하지 않는다.
4. F-002, F-003, F-011 완료 — PDF·원문 페이지 메타데이터 계약을 맞추고 챕터 설정의
   검증·확정 경계를 원자화했으며, 재개 시 실제 pending 결과만 처리하도록 정리했다.
5. 완료 — F-005, F-006, F-007로 fixture와 설치·검증·MCP 설정 적용 절차를 재현
   가능하게 만들었다.
6. F-004, F-008, F-012 완료 — 처리 모드 선택지의 자동 추천 표현, 오래된 문서 참조,
   실패 호출에 의존한 선택 흐름을 정리했다. 이후 중복 파일을 작은 리팩터링 단위로
   정리한다.
7. F-013 완료 — 렌더 완료 뒤 중간 작업 데이터만 안전하게 정리하는 경로를 분리했다.
8. F-014 완료 — HTML 자료를 플랫폼별 더블클릭 런처로 열고, 자동 포트·loopback
   서버·브라우저 시작을 제공하면서 직접 실행 호환 경로를 유지했다.
