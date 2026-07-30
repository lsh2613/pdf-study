# 서버 프로젝트 고정 결과 루트와 결과 조회

## 상태

채택

## 결정

- `init_work(pdf_path)`와 `resume_work(pdf_path)`는 `server.py`가 위치한 MCP 서버
  프로젝트 루트 아래 `result/<sanitized-pdf-stem>`만 사용한다.
- 요청의 Codex workspace, MCP file root, MCP 서버 프로세스 cwd는 출력 경로 계산에
  사용하지 않는다. 공개 `output_dir`도 다시 추가하지 않는다.
- `list_study_results()`는 입력 없는 읽기 전용 MCP 도구로 제공한다.
- 조회 응답의 `data.result_root`는 고정 result 루트의 절대 경로이고,
  `data.result_paths`는 정규화된 PDF 이름을 포함하는 직접 하위 디렉터리의 정렬된
  절대 경로 배열이다.
- 숨김 staging 경로, 일반 파일, 심볼릭 링크는 조회 결과에서 제외한다. result
  루트가 없으면 빈 배열을 반환하며 디렉터리나 상태 파일을 만들지 않는다.

이 결정은 0009의 요청 workspace 기준 출력 경로 결정과 0008의
`new_output_dir` 선택을 대체한다. 0009의 Elicitation과 공개 입력 제한,
0008의 명시적 resume/replace 및 manifest 소유권 결정은 계속 유효하다.

## 이유

요청 workspace와 MCP root는 클라이언트 및 호출마다 달라질 수 있고 일부
클라이언트에서는 제공되지 않는다. 결과 저장 위치를 서버 설치 프로젝트에 고정하면
동일한 서버에서 만든 결과가 한 `result` 루트에 모이며, workspace context 부재로
`init_work`가 실패하지 않는다.

고정 위치는 사용자의 현재 작업 디렉터리와 다를 수 있으므로, 서버가 실제 절대
경로를 직접 조회하는 도구를 제공해야 결과를 안정적으로 찾을 수 있다.

## 대안

- 요청의 단일 workspace 또는 MCP root 사용: 호출 context에 따라 저장 위치가
  달라지고 context가 없거나 모호하면 작업을 시작할 수 없어 채택하지 않았다.
- 프로세스 `Path.cwd()` 기준: 장시간 실행되는 stdio 서버의 시작 위치가 서버
  프로젝트라는 보장이 없어 채택하지 않았다.
- 결과 경로를 사용자 입력으로 받기: 에이전트가 임의 경로를 선택하고 기존 파일과
  충돌시킬 수 있어 공개 `output_dir`을 계속 두지 않는다.

## 결과

같은 PDF 이름은 같은 서버 프로젝트 안에서 항상 같은 출력 폴더와 충돌 정책을
사용한다. 사용자는 현재 workspace를 알 필요 없이 `list_study_results()`로 진행
중이거나 완료된 결과 경로를 찾을 수 있다.
