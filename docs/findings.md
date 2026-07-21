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

### F-004 [P1] 처리 모드 선택지에 “기본” 표현이 들어가 무기본값 규칙과 충돌한다

- 발생 조건: 처리 모드가 빠져 `set_chapters`가 네 가지 선택지를 반환한다.
- 관찰 증상: Sequential + Text의 설명과 오류 본문에 `무난한 기본`,
  `무난한 기본값`이 들어간다.
- 문서와의 차이: `docs/business-rules.md`, `docs/standards.md`, `AGENTS.md`는
  추천·기본값을 임의로 붙이지 않고 사용자의 명시 선택을 받도록 한다. 같은 함수
  설명도 “기본값 없음”이라고 명시한다.
- 영향: 비개발 사용자는 해당 선택지가 자동 적용되는 값이라고 오해할 수 있다.
- 가능한 접근: 네 선택지의 장단점만 중립적으로 기술하고 “기본” 표현을 제거한다.

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

### F-007 [P1] 로컬 MCP 설정 적용 위치와 보호 방식이 프로젝트 기준에 맞지 않는다

- 스크립트는 저장소 위치 `REPO_DIR`를 계산하지만 로컬 설정 경로에는 `$PWD`를
  전달한다.
- 기존 설정 JSON 파싱이 실패하면 오류를 내지 않고 빈 객체로 바꾼 뒤 덮어쓴다.
- 기본 실행은 설치 여부와 무관하게 세 클라이언트 설정을 모두 만들 수 있다.
- `.gitignore`의 “MCP 클라이언트별 로컬 설정” 항목은 실제 생성 경로인
  `.claude.json`, `.codex/mcp.json`, `.agents/mcp_config.json`을 제외하지 않는다.
- 문서와의 차이: `scripts/AGENTS.md`는 저장소 위치를 스스로 계산하고 사용자의 현재
  디렉터리에 의존하지 않도록 한다.
- 영향: 엉뚱한 프로젝트에 설정 파일이 생기거나 기존 설정이 유실될 수 있고 사용자
  절대 경로가 커밋될 수 있다.
- 가능한 접근: 로컬 기준을 `REPO_DIR`로 고정하고 JSON 파싱 실패 시 중단한다.
  원자적 저장과 백업을 사용하고 실제 생성 경로를 ignore한다.

### F-008 [P2] 코드 주석이 존재하지 않는 문서를 가리킨다

- `renderer/md_tui_renderer.py`가 `docs/05-data-schemas.md`와
  `docs/07-study-ui.md`를 참조하지만 두 파일은 저장소에 없다.
- 현재 계약의 출처를 찾을 수 없고 삭제된 설계를 현행 기준으로 오해할 수 있다.
- 참조를 `docs/contracts.md`, `docs/architecture.md` 등 현존 문서로 바꾸거나 필요한
  설계 문서를 복원해야 한다.

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

### F-012 [P2] 정상 선택지를 얻기 위해 일부러 실패 호출을 해야 한다

- 처리 모드의 구조화된 `data.choices`는 모드가 빠진 `set_chapters` 실패 응답에만
  있다.
- 출력 형식의 구조화된 선택지도 `finalize_study(output_format="")` 실패 응답에만
  있다.
- `scan_pdf.user_choices`는 label/desc 없는 문자열 배열이고 설명은 긴
  `next_step_guidance` 안에 섞여 있다.
- 영향: 실패가 정상 제어 흐름이 되어 비개발 사용자에게 오류처럼 보이고,
  클라이언트가 선택지 문구를 재작성할 가능성도 커진다.
- 가능한 접근: `scan_pdf`에 기존 처리 모드 선택지를, 완료된
  `list_pending_chapters`에 기존 출력 형식 선택지를 문구 변경 없이 포함한다.
  `user_choices`도 `{value,label,desc}` 구조로 통일한다.

### F-013 [P2] `.work` 삭제만 원해도 최종 렌더링을 다시 수행한다

- 기본 `keep_work_dir=true`로 결과를 만든 뒤 중간 데이터를 삭제하려면 같은
  `finalize_study(..., keep_work_dir=false)`를 다시 호출해야 한다.
- 두 번째 호출은 결과물을 다시 렌더링한 다음 `.work`를 지운다.
- 가능한 접근: 최초 finalize 전에 보존 여부를 받거나, 렌더 없이 정확한 작업
  폴더만 정리하는 `cleanup_work` 동작을 분리한다.

### F-014 [P2] 비개발 사용자도 결과를 열기 위해 터미널과 서버를 관리해야 한다

- HTML 진도 저장을 위해 `python3 study_html.py`를 실행하고 포트 충돌과
  `Ctrl+C` 종료를 직접 관리해야 한다. `file://`로 열면 핵심 진도 기능이 동작하지
  않는다.
- 프로젝트가 비개발자를 포함한다고 정의하지만 결과를 보는 마지막 단계는 개발자
  중심이다.
- 가능한 접근: MCP의 명시적 start/stop 도구, 더블클릭 가능한 플랫폼 런처, 또는
  서버 없이 동작하는 브라우저 저장 방식을 제공한다. 최소한 포트 자동 선택과 실행
  상태 확인을 자동화한다.

## 불필요하거나 중복 관리되는 파일·정의

| 대상 | 관찰 내용 | 정리 방향 |
|---|---|---|
| `templates/html/grading.js` | 실제 채점은 `storage.js`가 담당하며 이 파일은 6줄짜리 future placeholder다. 모든 HTML에 복사·로드되고 테스트도 존재만 강제한다. | 파일, 복사 목록, `<script>` 태그, 존재 테스트를 제거하고 실제 기능이 생길 때 다시 추가한다. |
| `docs/superpowers/plans/2026-07-20-single-korean-flow.md` | 바로 다음 커밋에서 구현이 끝난 계획인데 체크박스는 모두 미완료이고 현재 저장소에 없는 `superpowers:*` 스킬을 필수라고 적는다. | 역사 보존이 필요하면 완료/적용 커밋을 표시해 archive하고, 필요 없으면 삭제한다. |
| `docs/10-mcp-setup.md`와 `docs/operations.md` 설치 절 | venv 생성, 세 클라이언트 적용, `--print-config`, `--check`, uv/libomp 설명이 반복된다. | `operations.md`를 기준으로 두고 `10-mcp-setup.md`는 짧은 진입 안내와 링크만 남기거나 합친다. |
| `AGENTS.md`와 `CLAUDE.md` | 현재 바이트 단위로 동일하다. 두 진입 파일은 필요하지만 각각 수정하면 드리프트한다. | 하나의 canonical 원본에서 생성하거나 symlink를 사용하고 동등성 검사를 둔다. |
| `.claude/skills/commit/SKILL.md`와 `.codex/skills/commit/SKILL.md` | provider 경로와 co-author 3곳만 다르고 나머지 절차가 중복된다. | 공통 템플릿과 provider 변수로 생성하고 동기화 테스트를 둔다. |
| 문제 JSON 계약 | `prompts.py` 예시, `server.py` 수동 검증, `docs/contracts.md`, 테스트 fixture에 같은 스키마가 반복된다. | typed model 또는 JSON Schema 하나를 canonical로 두고 검증과 프롬프트 예시를 생성한다. |
| 처리 모드 선택지 | 함수 설명, `combos`, 오류 prose, `CHOICE_POLICY`, 여러 문서에 같은 문구가 반복된다. | 선택지 상수와 렌더 헬퍼를 한 곳에 두고 모든 응답에서 재사용한다. |
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
5. F-005, F-006, F-007로 설치와 검증 절차를 재현 가능하게 만든다.
6. 나머지 선택 흐름과 중복 파일을 작은 리팩터링 단위로 정리한다.
