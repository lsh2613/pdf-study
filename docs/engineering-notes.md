# 작업 메모

## 깨진 텍스트 레이어

증상: PDF에 글자가 보이는데 text 모드 결과가 무의미한 문자열, 비정상 기호, 섞인 한글·라틴 조각으로 나온다.

원인: 일부 PDF는 화면 렌더링은 되지만 ToUnicode 매핑이 깨져 PyMuPDF 텍스트 추출이 실제 본문을 복원하지 못한다.

대응: `evaluate_text_quality`의 모지바케 점수를 거쳐 `garbled`이면 text 모드를 거부한다. 관련 변경 후에는 깨진 샘플과 정상 한글·영문·표 샘플이 각각 올바르게 분류되는지 테스트한다.

## 목차와 본문 추출은 다른 결정이다

증상: “OCR PDF니까 목차도 OCR로만 해야 한다” 또는 “내장 목차가 있으니 본문도 text로 읽어도 된다”는 식의 변경이 들어가기 쉽다.

원인: 챕터 경계는 내장 북마크나 목차 이미지가 담당하고, 본문 입력 방식은 텍스트 레이어 품질과 사용자 선택이 담당한다. 두 판단은 서로 독립이다.

대응: `scan_pdf`는 챕터 후보와 텍스트 품질을 모두 반환하지만, 본문 처리 모드는 `set_chapters`에서 확정한다. 내장 목차가 없으면 `scan_pdf`는 목차 후보 이미지만 렌더하고, OCR 모델 준비는 `prepare_ocr`, 목차 이미지 OCR은 `scan_toc_with_ocr`가 맡는다. 변경 후에는 내장 목차가 있는 OCR 모드와 내장 목차가 없는 text 가능 PDF를 모두 확인한다.

## OCR 모델 준비 단계

증상: `scan_pdf`가 오래 응답하지 않으면 사용자는 PDF 스캔이 멈춘 것으로 판단한다.

원인: PaddleOCR 첫 실행은 모델 다운로드와 모델 로드가 필요할 수 있다. 이 작업이 `scan_pdf` 안에 숨어 있으면 장시간 대기의 이유가 드러나지 않는다.

대응: `scan_pdf`에서는 PaddleOCR 모델 다운로드, 모델 로드, OCR 실행을 하지 않는다. 첫 모델 다운로드는 `prepare_ocr`에서만 수행하고, 목차 이미지 OCR은 `scan_toc_with_ocr`에서 수행한다. 모델 캐시가 이미 있으면 `scan_toc_with_ocr`와 `set_chapters`의 본문 추출 Elicitation에서 OCR을 선택한 흐름의 내부 모델 로드는 허용한다.

## Elicitation 선택지 경계

증상: 선택 파라미터나 구조화 fallback이 공개 계약에 남으면 에이전트가 form을 열지
않는 경로를 시도할 수 있다.

원인: 응답 지시나 일반 도구 인자는 실제 사람의 답에서 왔는지 서버가 검증할 수 없다.

대응: 선택값은 등록된 MCP async 함수가 여는 form Elicitation에서만 받는다. 공개
스키마에서 선택 파라미터를 제거하고, 공개 응답에서도 `choices`,
`user_choice_required`, `user_choice_instruction`, `question_setup`,
`ocr_language_setup` 같은 fallback을 제거한다. 선택 정의는 Elicitation form을
구성하는 private helper 안에만 둔다. 공개 도구 설명, 오류와 `next_action`도 실제
등록된 입력만 예시로 사용하고, 선택이 필요하면 도구가 form을 연다고 안내한다.
번호형 자유 텍스트 선택지는 구조화 fallback과 같은 우회 경로로 취급한다.

## Codex가 Elicitation 요청을 method not found로 거절

증상: 클라이언트 capability에는 Elicitation이 있지만 `ctx.elicit`에서
`McpError: elicitation/create`가 발생하고 form이 열리지 않는다.

원인: Pydantic의 `str | None`은 `anyOf=[string, null]`을 만들고 모델 기본
JSON Schema는 최상위 `title`도 만든다. 둘 다 Codex의 엄격한 MCP primitive form
계약 밖이라 요청이 도구 실행 전에 거절될 수 있다.

대응: 모든 정적·동적 form 모델은 공통 Elicitation 기반 모델을 상속한다. 이 기반은
최상위 `title`을 제거한다. 선택 입력은 `str`과 빈 문자열 기본값으로 표현하고, 새
`init_work`의 학습자 정보는 `str`, `required`, `minLength=1`, default 없음으로
표현한다. FastMCP round-trip 테스트에서 실제 `requestedSchema`의 최상위 키, 각
필드의 primitive `type`, nullable `anyOf`와 `$ref` 부재, 학습자 정보의 필수 계약을
검사한다. Codex 세션 자체의 승인 정책이 `never`이면 유효한 form도 자동 거절되므로
서버 스키마 오류와 구분한다.

선택지별 표시명과 내부값을 나누려고 `enumNames`를 보내면 현재 FastMCP 요청 모델이
`Invalid request parameters`로 거절한다. 따라서 지원되는 단순 문자열 `enum` 값
자체를 `한글 이름 — 요약 설명`으로 만들고, 승인 뒤 같은 Elicitation 함수에서
기존 내부값으로 변환한다. 필드 `title`은 한국어로 명시하되 form 최상위 모델
`title`은 계속 제거한다. 선택지 설명은 `message`에 중복하지 않는다.

Codex 클라이언트는 다중 필드 form의 탐색 순서를 서버의 속성 선언 순서와 다르게
표시할 수 있다. 문제 유형처럼 순서가 계약인 입력은 하나의 다중 필드 form에 넣지
않고 단일 필드 form을 `단답형 → 주관식 → 확장형` 순서로 호출한다. 각 form은
질문·설명을 `message`, 짧은 항목명을 `title`에 넣는다. 모든 선택은 메모리에만
모았다가 필요한 form이 전부 승인된 뒤 상태를 변경한다.

## Python 직접 실행으로 Elicitation 우회

증상: MCP 입력 스키마에서 선택 파라미터를 제거했는데도 셸 권한이 있는 에이전트가
`pdf_learner.server`를 import하고 동기 워크플로 함수를 직접 호출해 문제 유형,
text/OCR, 순차/병렬, 출력 형식을 임의로 정한다.

원인: MCP Elicitation은 MCP 요청 경계에서만 실행된다. 같은 이름의 동기 구현이
모듈 속성으로 공개되어 있으면 Python 직접 호출은 비동기 MCP 래퍼와 form을 거치지
않는다.

대응: 선택을 소비하는 7개 워크플로는 각각 등록된 async MCP 함수 하나로만
구현한다. 이 함수 안에서 capability 확인, form Elicitation, 응답 검증, 승인 후
실행을 연속 처리한다. 같은 작업을 선택 인자로 바로 수행하는 `_impl` 함수나 별도
MCP wrapper는 만들지 않는다. 회귀 테스트는 7개 등록 함수가 async이고 `ctx`를
요구하며, 대응하는 `_{name}_impl` 속성이 없고 공개 MCP 스키마에는 선택 인자가
없는지 함께 확인한다.

한계: 이 구조는 동일한 선택형 워크플로의 직접 호출 우회를 제거하지만 OS 보안
경계는 아니다. 동일 사용자 권한으로 임의 Python·셸·파일 쓰기가 가능한 에이전트를
반드시 막아야 한다면 MCP 서버와 결과 폴더를 별도 OS 사용자 또는 샌드박스로
분리해야 한다. 하위 primitive, 내부 토큰, 환경 변수, call-stack 검사는 그런
권한에 대한 강제 수단으로 취급하지 않는다.

## 에이전트가 필수 선택을 대신 결정

증상: 에이전트가 간헐적으로 사용자에게 묻지 않고 임의 값을 다음 도구에 전달한다.

원인: 응답 문구는 에이전트 행동 지침일 뿐, 일반 도구 인자가 실제 사람의 답에서
왔는지 서버가 검증할 수 없다.

대응: 등록된 async MCP 함수가 문제 유형, OCR 언어, 챕터 구성·범위와 처리 방식,
출력 형식, 기존 작업 재개·교체와 `.work` 정리를 실행 직전에 묻고, 같은 함수
안에서 승인 후 처리까지 이어간다. 선택 파라미터 자체가 공개 입력에 없으며
거절·취소 또는 미지원 세션이면 처리 본문을 실행하지 않는다. 새 작업 form에는
계산된 절대 출력 폴더를 안내하되 별도 확인 boolean은 요구하지 않는다.

`set_chapters`는 챕터 구성 방식, 본문 추출 방식, 실행 방식 form을 순서대로 연다.
직접 입력과 균등 청크는 각각 범위 입력 또는 청크 크기 후속 form을 조건부로 연다.
앞선 form의 승인값은 메모리에만 두고 필요한 form이 모두 승인된 뒤에만 처리 상태를
변경한다. 따라서 중간 form에서 취소해도 rollback할 상태 변경이 없다. FastMCP form
schema는 primitive 필드만 허용하므로 문자열 선택은
`str` 필드의 JSON Schema `enum`으로 표시하고, 수신값을 서버 허용 목록으로 다시
검증한다.

## 출력 폴더가 요청마다 이동

증상: 같은 PDF의 결과가 요청 workspace, MCP root, MCP 서버를 시작한 cwd에 따라
서로 다른 `result/<pdf-name>`으로 생성되어 사용자가 결과를 찾기 어렵다.

원인: 요청 context나 프로세스 `Path.cwd()`는 서버 설치 위치와 별개이며 클라이언트와
호출마다 달라질 수 있다.

대응: `server.py`의 실제 위치를 MCP 서버 프로젝트 루트로 삼고, 공개 `output_dir`
없이 그 아래 `result/<pdf-name>`만 사용한다. 요청 workspace, MCP root, 프로세스
cwd는 경로 계산에 참여하지 않는다. `list_study_results`는 같은 고정 result 루트의
직접 하위 디렉터리를 정렬된 절대 경로로 반환한다. 조회 결과에서는 숨김 staging,
일반 파일, 심볼릭 링크를 제외하고 파일 시스템 상태를 변경하지 않는다.

## 챕터 설정 검증과 처리 상태

증상: `set_chapters`가 스캔 전 호출이나 잘못된 페이지 범위·중복 ID로 실패했는데 처리 모드만 바뀌거나, 유효한 설정 뒤 본문 추출이 실패했을 때 setup 실패와 처리 실패를 구분할 수 없다.

원인: 모드, 챕터 목록, 책 정보를 별도 저장하고 본문 준비 phase를 종결하지 않으면 하나의 요청이 부분 적용된 상태로 보인다.

대응: 분석 계층은 스캔 여부와 모든 챕터 정의를 순수 검증하고 책 정보 fallback까지 계산한 뒤 `workspace.commit_chapter_setup`을 호출한다. 이 헬퍼는 작업 잠금 안에서 책 정보를 먼저 atomic write하고 모드·챕터·phase를 한 번의 state 저장으로 확정하며, state 저장이 실패하면 책 정보를 복원한다. 복원도 실패하면 transaction 오류로 드러낸다. 같은 `work_id`의 setup과 본문 준비 전체는 별도 잠금으로 직렬화해 먼저 시작한 호출이 나중 설정의 raw나 phase를 갱신하지 못하게 한다. 확정 이후의 추출 실패는 새 설정을 유지한 채 `chapter_processing=failed`로 남긴다. 변경 후에는 검증 실패 전후 상태·책 정보 동일성, setup 단일 저장, 책 정보 rollback과 rollback 실패, 같은 작업의 동시 setup, 성공·OCR 실패·예기치 않은 예외의 phase를 함께 검사한다.

## OCR 본문 선계산 저장

증상: OCR 모드로 챕터를 등록했지만 raw 원문에 본문 텍스트가 없거나 일부 페이지만 저장되면 이후 진단이나 재렌더에서 text 모드와 다르게 동작한다.

원인: OCR은 페이지 이미지 렌더링과 PaddleOCR 호출을 거치므로 페이지 단위 예외나 전체 공백 결과가 발생할 수 있다. 이때 partial 본문을 저장하면 실패 챕터가 정상 raw처럼 보인다.

대응: OCR 모드는 `set_chapters` 반환 전에 non-skip 챕터를 페이지 순서대로 OCR하고, 챕터 전체가 성공했을 때만 `chapters_raw/chN.json`에 `text`와 `char_count`를 저장한다. 페이지 OCR 예외나 전체 공백 결과는 `summary_status=failed`와 `error`로 드러내고 raw 본문을 저장하지 않는다.

## 완료 상태의 거짓 양성

증상: 서브 에이전트가 성공했다고 말했지만 요약, 핵심 포인트, 문제 배열 중 일부가 비어 있는데 챕터가 완료로 표시된다.

원인: 상태 전환이 저장 호출 자체에만 묶이면 payload 내용이 비어 있어도 완료가 된다.

대응: 서버 경계에서 활성 문제 유형별 필수값을 검사한 뒤에만 `completed`로 바꾼다. 새 문제 유형이나 저장 스키마를 추가하면 누락 검사를 먼저 확장해야 한다.

요약 문자열과 핵심 포인트가 비어 있지 않은 것만으로는 의미 보존을 확인할 수 없다.
요약 전에는 챕터 전체에서 실제 제목·순서·계층만 `section_inventory`로 만들고,
명시적인 서브 챕터가 없으면 챕터 전체 section 하나만 둔다. inventory를 내용 필터로
쓰지 않는다. inventory 분석자는 exact source anchor만 기록하고 본문을 복사하지 않으며,
서버가 반환한 번호형 후보를 section 선택 또는 근거 있는 제외로 모두 감사한다. 챕터
전체 판정이나 후보 제외는 요약 전에 별도 section 검토가 통과해야 한다. 설명되지 않은
후보나 미해결 구조 검토는 단일 chapter fallback으로 숨기지 않는다.
`get_section_content`가 canonical raw를 무손실 span으로 나눈다. 요약 프롬프트는
structured section의 모든 source text를 읽고 명시적 서브 챕터를 순서·계층대로
Markdown에 반드시 반영하게 한다. 요약 후에는
원문·초안을 대조한 `summary_review`가 챕터 전체의 중요 누락·왜곡 부재를 확인한 뒤에만
`passed`가 된다. section 구조는 검토와 저장 단계에서 다시 검증하지 않는다.
글자 수나 원문 대비 압축률은 문서별 정보 밀도를 반영하지 못하므로 품질 게이트로
사용하지 않는다.

구조를 여러 단계에서 다시 추론하면 OCR과 긴 원문의 불규칙성 때문에 실제 요약 품질과
무관한 false negative가 생길 수 있다. 따라서 저장 시 raw 제목의 의미를 다시 판단하거나
Markdown heading 포함 여부를 검사하지 않는다. prepared binding이 있으면 raw hash와
연속 span coverage만 검증한다. anchor가 반복되면 occurrence로 실제 본문 제목을 고르고,
찾지 못하거나 순서가 뒤집히면 요약 전에 inventory를 다시 분석한다.

분리 workflow의 학습자 정보는 문제의 난이도·표현·예시·관점에만 사용한다.
inventory·요약·검토에 주입하면 관심 분야와 겹치는 내용만 남기는 잘못된 압축을
유도할 수 있으므로 분리한다.
호환용 결합 프롬프트에서는 요약을 먼저 전체 원문 기준으로 확정한 뒤 문제 단계에서만
학습자 정보를 적용한다. `source_char_count`도 agent 입력을 신뢰하지 않고 raw
`char_count`와 실제 text 길이·상태 값을 대조한 뒤 canonicalize한다. 저장 전에 읽은
state와 이후 잠금 저장 사이에 `set_chapters`가 다시 실행되는 경쟁은
`chapter_setup_generation`을 잠금 안에서 재검사해 오래된 결과가 새 설정을 완료로
바꾸지 못하게 한다.

## 재개 시 완료 결과 재처리

증상: 일부 챕터의 요약이나 확장 문제만 남은 작업을 재개했는데 모든 non-skip 챕터를 다시 읽고 두 결과를 모두 저장하라는 안내가 나온다. 완료 챕터의 raw 파일이 사라졌을 때 남은 작업과 무관한 검증 오류로 재개가 막힐 수도 있다.

원인: 처리 대상을 하나의 전체 챕터 목록으로 만들면 요약 pending과 확장 pending이 서로 다른 상태를 표현할 수 없고, raw 검증과 다음 작업 안내도 완료 여부를 구분하지 못한다.

대응: 한 상태 스냅샷에서 `summary_pending_chapter_ids`와 `extension_pending_chapter_ids`를 각각 계산하고, 호환용 `chapter_ids`는 두 목록의 자연 정렬 합집합으로 만든다. raw 검증은 원문이 필요한 summary pending에만 적용한다. 요약은 완료되고 extension만 pending이면 raw를 다시 요구하지 않고 저장된 `summary`, `key_points`, `source_char_count`를 검증한다. workflow, `get_subagent_prompts`와 챕터별·전체 `next_action`은 실제로 남은 결과 유형의 save 도구와 올바른 입력 조회 도구만 안내한다. 변경 후에는 두 pending 집합이 다른 재개, extension-only에서 raw 누락, 저장 요약 누락, 확장 비활성, 모든 결과 완료를 함께 확인한다.

## 문제 생성에 원문이 다시 섞임

증상: 요약본을 복습하기 위한 문제인데도 요약에서 생략한 원문 세부 정보가 문제,
보기, 정답이나 해설에 나타난다.

원인: 같은 생성 단계나 sub-agent에 원문과 요약을 함께 전달하면 문제 프롬프트가
요약 근거 제한을 적어도 모델이 원문의 세부 내용을 다시 사용할 수 있다.

대응: 원문은 section inventory, 요약 작성과 독립 검토까지만 전달한다. 검토 통과 뒤 기본
문제 생성 단계에는 `summary`, `key_points`, `source_char_count`만 전달한다. 확장
문제는 `get_chapter_summary`가 챕터 식별 메타와 함께 같은 세 입력 필드를 반환하며
원문, section inventory, 검토 내부 정보는 노출하지 않는다. `source_char_count`는 문제
개수 상한 계산에만 쓴다.

## 결과 파일과 상태 저장 순서

증상: 잘못된 챕터 ID나 건너뛰기 챕터에 저장을 시도했거나, 파일 저장 뒤 상태 저장이 실패했는데 `chapters/summaries`, `chapters/quiz`, `chapters/extension_quiz`에 JSON 파일이 남는다.

원인: 결과 파일을 먼저 쓰고 나중에 상태를 확인하거나 갱신하면 실패한 요청의 파일만 디스크에 남을 수 있다. 이후 렌더러나 재시도 흐름이 이 파일을 정상 결과로 오해할 수 있다.

대응: 저장 헬퍼는 작업별 잠금 안에서 상태를 먼저 읽어 대상 챕터가 존재하고 skip이 아닌지 확인한 뒤 파일을 쓴다. 파일을 쓴 뒤 `state.json` 저장이 실패하면 새 파일은 삭제하고 기존 파일은 실패 전 바이트로 복원한다. 관련 변경 후에는 unknown chapter, skipped chapter, state save failure 케이스에서 결과 파일이 남지 않는지 확인한다.

## 출력 폴더 충돌과 렌더 세대

증상: 같은 `output_dir`에서 작업을 다시 시작하거나 출력 형식·챕터 수를 바꾸면 이전 raw, 요약, 문제, HTML/TUI 파일과 진도가 새 자료에 섞인다.

원인: `state.json`과 현재 필요한 파일만 덮어쓰고, 이전 세대에서 생성했지만 이번 세대에는 없는 파일의 소유권과 제거 범위를 기록하지 않으면 안전하게 정리할 수 없다. 파일 존재만 보고 렌더 데이터를 읽으면 부분 렌더링이 pending 챕터의 예전 JSON을 정상 결과로 오해할 수도 있다.

대응: `init_work`는 고정 출력 폴더의 기존 관리 작업을 발견하면 상태를 바꾸기 전에
`resume`, `replace` Elicitation을 연다. 관리되지 않은 파일은 실패한다. 명시적
replace도 새 입력을 먼저 검증하고 `.work`만 제거하며 이전 렌더 결과는 다음 렌더
성공까지 둔다. 렌더는 staging에서 끝까지 만든 뒤 `.pdf-learner-manifest.json`의
관리 경로만 rollback 가능한 순서로 교체한다. manifest 밖의 파일은 제거하거나
덮어쓰지 않는다.

완료 결과 뒤 `.work`만 정리할 때는 `cleanup_work`가 작업 잠금 안에서 rendering 완료 상태를 확인하고 해당 디렉터리만 삭제한다. 이 경로는 렌더러나 manifest 교체를 호출하지 않으며, 결과 파일·진도·사용자 파일을 보존한다.

진도는 manifest의 `output_format`과 `study_fingerprint`가 모두 현재 값과 같을 때만 복사한다. fingerprint는 PDF 식별 정보, 책 정보, 문제 옵션, non-skip 챕터 메타와 완료된 요약·문제 payload를 포함한다. 이 경계를 바꾼 뒤에는 챕터 감소, 형식 전환, 내용 변경, 렌더 예외, 사용자 파일 충돌을 함께 테스트한다.

## HTML 마크다운 폴백

증상: `markdown-it-py`가 없는 환경에서 요약의 `##`, `**bold**`, 표 문법이 그대로 화면에 보인다.

원인: HTML 렌더링은 마크다운 변환에 의존하지만, 생성된 학습 자료를 보는 환경이 서버 개발 환경과 다를 수 있다.

대응: `HtmlRenderer`는 import 실패 시 최소 변환기를 사용한다. 렌더러 변경 후에는 굵게, 인라인 코드, 표, 이중 이스케이프된 줄바꿈이 HTML로 변환되는지 확인한다.

## HTML 더블클릭 실행

증상: 비개발 사용자가 HTML 학습 자료의 진도 저장을 위해 터미널에서 고정 포트 서버를 직접 시작해야 하고, 포트 충돌도 직접 해결해야 한다.

대응: HTML 결과에는 렌더링한 프로젝트 환경을 가리키는 `start_study.sh`와 `start_study.bat`을 함께 넣는다. 두 런처는 `study_html.py --port 0`을 실행해 loopback 주소의 사용 가능한 포트를 자동 배정하고 브라우저를 연다. 생성 완료 응답은 두 파일명과 자동 포트 여부를 알리며, 기존 직접 실행 정보(`launch_command`, `python`, `entry_page`, 고정 포트 `default_url`)는 호환을 위해 유지한다. 진도는 계속 `progress/` 아래 JSON만 읽고 쓴다. 런처와 생성물은 같은 컴퓨터의 프로젝트 환경에서만 실행할 수 있으므로, 다른 컴퓨터로 옮긴 경우에는 해당 환경을 다시 준비해야 한다.
