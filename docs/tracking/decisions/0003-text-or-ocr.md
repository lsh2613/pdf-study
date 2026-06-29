# 본문 입력 모드 분리

## 배경

PDF에는 신뢰 가능한 텍스트 레이어가 있는 파일, 텍스트가 거의 없는 스캔본, 화면은 정상인데 추출 문자가 깨지는 파일이 섞여 있다. 한 가지 방식으로 본문을 읽으면 일부 파일에서 학습 자료 품질이 무너진다.

## 결정

본문 입력은 text 모드와 OCR 모드를 명시적으로 나눈다. text 모드는 서버가 PyMuPDF로 본문을 추출한다. OCR 모드는 서버가 본문 페이지 이미지를 렌더하고 PaddleOCR CPU로 읽은 뒤 `chapters_raw/chN.json`에 `text`와 `char_count`를 저장한다. 요약자는 두 모드 모두 `get_chapter_content`가 반환한 `text`를 입력으로 사용한다.

## 대안

- 항상 text 모드: 빠르고 저렴하지만 스캔본과 모지바케 PDF에서 실패한다.
- 항상 OCR 모드: 깨진 PDF까지 처리할 수 있지만 느리고 CPU 부하가 커진다.
- 클라이언트 vision OCR: 서버 의존성은 줄지만 raw 본문 저장과 실패 검증이 클라이언트 모델 출력에 묶인다.

## 결과

사용자는 품질과 처리 시간 사이에서 명시적으로 선택한다. 서버는 텍스트 품질이 나쁠 때 text 모드를 막을 수 있다. OCR 모드에서는 `set_chapters`가 sub-agent 호출 전에 raw 본문을 준비해야 하며, raw `text`와 `char_count`가 없거나 불일치하면 `get_subagent_prompts`와 `get_chapter_content`가 거부한다.
