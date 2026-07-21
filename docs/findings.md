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

### F-003 [P1] `set_chapters`가 입력 검증을 끝내기 전에 상태를 변경한다

- 발생 조건: `scan_pdf` 전 호출, 빈/중복/범위 밖 챕터 등의 이유로
  `set_chapters`가 거부된다.
- 관찰 증상: `analysis.set_chapters_impl`은 `page_count`와 챕터 정의를 검증하기 전에
  `execution_mode`와 `extraction_mode`를 상태에 쓴다. 챕터 정의 검증 뒤에는 본문
  추출이 성공하기 전에 기존 `state.chapters`를 새 목록으로 교체한다.
- 문서와의 차이: 계약은 `set_chapters`가 챕터와 처리 모드를 “확정”한다고 설명하지만,
  실패 응답 뒤에도 일부 선택이 확정된 상태가 남을 수 있다.
- 영향: 재시도 전에 상태가 예상과 다르게 바뀌며 기존 완료 정보도 조기에 대체된다.
- 가능한 접근: 순수 검증을 먼저 끝내고, 검증 완료 후 하나의 잠금 구간에서 상태를
  전환한다. 검증 완료 상태와 OCR 처리 실패 상태를 분리한다.

### F-004 [P1] 처리 모드 선택지에 “기본” 표현이 들어가 무기본값 규칙과 충돌한다

- 발생 조건: 처리 모드가 빠져 `set_chapters`가 네 가지 선택지를 반환한다.
- 관찰 증상: Sequential + Text의 설명과 오류 본문에 `무난한 기본`,
  `무난한 기본값`이 들어간다.
- 문서와의 차이: `docs/business-rules.md`, `docs/standards.md`, `AGENTS.md`는
  추천·기본값을 임의로 붙이지 않고 사용자의 명시 선택을 받도록 한다. 같은 함수
  설명도 “기본값 없음”이라고 명시한다.
- 영향: 비개발 사용자는 해당 선택지가 자동 적용되는 값이라고 오해할 수 있다.
- 가능한 접근: 네 선택지의 장단점만 중립적으로 기술하고 “기본” 표현을 제거한다.

### F-005 [P1] 테스트 상태 문서와 실제 재현 절차가 맞지 않는다

- `docs/tracking/status.md`는 최근 223개 테스트 통과를 기록하지만 현재 clean
  checkout에서 수집·통과하는 테스트는 210개다.
- F-001 처리 과정에서 상태 문서의 검증 개수는 현재 수집되는 210개로 교정했지만,
  아래 stale fixture 재현성 문제는 이 항목에서 해결하지 않았다.
- clean archive에서 `PYTHONPATH=<parent> python3 -m pytest -q`를 실행한 결과는
  `210 passed, 5 warnings`였다.
- 현재 작업 폴더에서는 `23 failed, 187 passed`였다. 남아 있던 ignored fixture
  `tests/fixtures/ko_with_toc.pdf`에는 내장 목차가 없었지만 현재 생성기로 새로 만든
  파일에는 3개 북마크가 있었다.
- `tests/conftest.py`는 fixture가 없을 때만 재생성하므로 생성기 변경 뒤의 stale
  fixture를 감지하지 못한다.
- 영향: 같은 커밋에서도 개발자마다 테스트 결과가 달라지고 상태 문서를 신뢰하기
  어렵다.
- 가능한 접근: fixture 생성 버전/해시가 달라지면 재생성하거나 테스트마다 임시
  디렉터리에 생성한다. 상태 문서에는 검증 커밋·명령·실제 개수를 함께 기록한다.

### F-006 [P1] 설치 후 문서에 적힌 `.venv` 테스트 명령을 바로 실행할 수 없다

- 발생 조건: 새 checkout에서 `./scripts/setup_mcp.sh`만 실행한 뒤
  `.venv/bin/python -m pytest`를 실행한다.
- 관찰 증상: 설치 스크립트는 런타임 의존성만 설치하고 pytest는 설치·검증하지
  않지만 `docs/operations.md`는 같은 `.venv`에서 pytest를 실행하도록 안내한다.
- 추가 불일치: `pyproject.toml`에는 `[project.optional-dependencies].dev`의
  `pytest>=8.0`과 `[dependency-groups].dev`의 `pytest>=9.1.1`이 중복되어 있다.
  `pytest-mock`도 선언되어 있지만 현재 테스트에서 사용하지 않는다.
- 가능한 접근: dev 의존성 정의를 하나로 통합하고 `setup_mcp.sh --dev` 또는
  `uv sync --group dev` 절차를 문서화한다.

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

### F-010 [P1] 초 단위 `work_id`가 동시 작업을 구분하지 못한다

- 발생 조건: 같은 서버 프로세스에서 1초 안에 두 번 `init_work`를 호출한다.
- 관찰 증상: `workspace.make_work_id`는 `YYYYMMDD-HHMMSS`만 사용한다. 두 작업의 ID가
  같으면 `_registry[work_id]`가 마지막 작업 폴더로 덮이고 잠금도 공유된다.
- 영향: 먼저 만든 작업의 후속 호출이 다른 PDF/출력 폴더를 읽고 쓸 수 있다.
- 가능한 접근: UUID/ULID 또는 랜덤 suffix를 사용하고 등록 시 기존 ID가 있으면
  거부한다.

### F-011 [P1] 재개 흐름이 완료된 챕터까지 다시 처리하도록 유도한다

- 발생 조건: 일부 챕터를 완료한 작업을 `resume_work`로 재개한다.
- 관찰 증상: `resume_work`는 pending 목록만 처리하라고 안내하지만
  `get_subagent_prompts`의 `chapter_ids`와 workflow는 모든 non-skip 챕터를 순회하라고
  한다. 요약 pending과 extension pending이 서로 다른 경우도 한 목록으로 표현할 수
  없다.
- 영향: 완료된 결과를 다시 생성·덮어쓰며 시간과 토큰을 낭비한다.
- 가능한 접근: `summary_pending_chapter_ids`와
  `extension_pending_chapter_ids`를 별도로 반환하고 workflow가 해당 목록만 처리하게
  한다.

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
3. F-010으로 동시 작업의 `work_id` 충돌을 제거한다.
4. F-002 완료 — PDF·원문 페이지 메타데이터 계약을 맞췄다. F-003과 F-011은
   사용자가 다음 항목으로 진행하라고 할 때까지 시작하지 않는다.
5. F-005, F-006, F-007로 설치와 검증 절차를 재현 가능하게 만든다.
6. 나머지 선택 흐름과 중복 파일을 작은 리팩터링 단위로 정리한다.
