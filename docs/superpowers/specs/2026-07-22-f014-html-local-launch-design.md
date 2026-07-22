# F-014 HTML 결과물 로컬 실행 설계

## 목적

HTML 학습 자료를 만드는 사용자가 MCP나 터미널 명령을 직접 다루지 않아도 결과 폴더의
스크립트로 학습 사이트를 실행하게 한다. 실행 시 포트 충돌을 피하고 브라우저를 자동으로
열며, 기존 `progress/` JSON 저장 API와 결과물의 로컬 데이터 경계를 유지한다.

## 범위와 전제

- 대상은 이 프로젝트를 설치·설정한 **같은 컴퓨터**의 HTML 결과물이다.
- 결과 폴더를 다른 컴퓨터나 이 프로젝트 밖으로 옮긴 뒤 실행하는 것은 지원하지 않는다.
- 서버 시작은 MCP 도구에 의존하지 않는다.
- HTML 진도는 계속 결과 폴더의 `progress/` JSON에 저장한다. `file://` 브라우저 저장소로
  대체하지 않는다.

## 선택한 접근

HTML 렌더 결과에 다음 세 파일을 함께 만든다.

- `study_html.py`: localhost 정적 파일·progress API 서버
- `start_study.sh`: macOS/Linux용 실행 스크립트
- `start_study.bat`: Windows용 실행 스크립트

렌더러는 결과를 만든 현재 프로젝트의 `sys.executable` 절대 경로를 두 실행 스크립트에
안전하게 인용해 넣는다. 따라서 사용자가 별도 Python을 설치하거나 터미널에서 인터프리터
경로를 찾을 필요가 없다. 실행 스크립트는 자신의 결과 폴더에서 `study_html.py`를 실행하고
추가 인자는 그대로 전달한다.

`study_html.py`의 기존 기본 포트 `8765`는 직접 실행 호환을 위해 유지한다. 새 실행
스크립트는 `--port 0`을 전달한다. 서버가 `127.0.0.1:0`에 직접 bind한 뒤
`server.server_port`로 실제 할당 포트를 읽고, 그 URL로 브라우저를 연다. 사용자가
실행 스크립트에 `--port 12345`를 추가하면 그 값이 자동 포트보다 우선한다. 포트 탐색을
셸에서 따로 수행하지 않으므로 탐색과 bind 사이의 경쟁 상태가 없다.

## 구성과 데이터 흐름

```text
start_study.sh / start_study.bat 더블클릭
→ 렌더 시 기록된 프로젝트 .venv Python 실행
→ 결과 폴더의 study_html.py 실행
→ 127.0.0.1:0 bind
→ 운영체제가 사용 가능한 포트 할당
→ 실제 URL로 기본 브라우저 열기
→ 브라우저의 /api/progress 요청이 결과 폴더 progress/에 저장
```

셸 스크립트는 foreground로 서버를 실행한다. 사용자는 시작 명령을 입력할 필요가 없고,
서버 창을 닫거나 Ctrl+C를 누르면 서버가 종료된다. Windows 배치 파일도 같은 동작을 한다.

## 안전성과 호환성

- 서버 bind 주소는 기존처럼 loopback `127.0.0.1`로 제한한다.
- `study_html.py`는 결과 폴더 밖을 읽거나 쓰지 않으며 progress API의 안전한 키 검증을
  유지한다.
- `.sh` 파일은 staging에서 실행 비트를 부여해 결과에 복사한다. `.bat`은 Windows의 기본
  연결로 실행한다.
- 새 파일은 HTML 렌더 세대의 관리 경로로 manifest에 포함된다. 형식 전환·재렌더·rollback은
  기존 output manager 경계를 그대로 사용한다.
- 절대 Python 경로는 의도적으로 결과의 이동성을 제한한다. 경로가 사라진 경우 스크립트는
  명확한 실행 오류를 보이며, 다른 Python을 임의로 찾아 실행하지 않는다.

## 외부 계약과 문서

`finalize_study`의 HTML 성공 응답은 사용자가 복사해 실행할 터미널 명령 대신
`start_study.sh`와 `start_study.bat`의 실행 방법, 자동 할당 URL의 성격, 종료 방법을
안내한다. Markdown+TUI 출력 계약은 바꾸지 않는다.

`docs/contracts.md`, `docs/operations.md`, `docs/architecture.md`,
`docs/business-rules.md`, `docs/engineering-notes.md`, `docs/tracking/status.md`,
`docs/findings.md`와 HTML 결과물 README를 현재 동작으로 갱신한다.

## 테스트와 완료 조건

다음 회귀를 추가한다.

1. 포트를 지정하지 않은 `study_html.py`가 사용 가능한 포트에 bind하고 실제 URL을 출력한다.
2. 명시한 포트는 계속 사용한다.
3. HTML 렌더 결과가 두 실행 스크립트를 포함하고 `.sh`에 실행 비트가 있다.
4. 두 스크립트가 렌더 시 사용한 Python 절대 경로와 결과 폴더의 `study_html.py`를 인용해
   실행한다.
5. HTML 결과의 manifest 교체가 새 실행 스크립트도 관리하고, 기존 progress API 테스트가
   계속 통과한다.

F-014는 스크립트 실행이 MCP 없이 자동 포트·브라우저 열기로 동작하고, 결과·진도 안전성,
전체 테스트, 관련 문서 갱신과 커밋까지 완료된 뒤 해결로 기록한다.
