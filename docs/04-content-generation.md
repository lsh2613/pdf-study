# 04. 콘텐츠 생성 (문제 + Sub-agent)

## 검증 문제 4유형

| 유형 | 채점 |
|---|---|
| 객관식 (mc) | 자동 채점 — **맞춘 개수 / 전체 개수**만 표시 (점수 환산 없음) |
| 단답형 (sa) | 모범답안 토글 (자가 확인) |
| 주관식 (rf) | 모범답안 토글 (자가 확인) |
| 확장형 (ex, Exa 검색) | 모범답안 토글 + 출처 링크 |

- 점수 환산/가중치 개념 없음. 객관식은 `2/3 맞음` 형태로만 표시.
- 단답/주관/확장은 사용자가 입력 후 "모범답안 보기" 버튼으로 확인한다. HTML에서는
  같은 버튼으로 다시 접을 수 있다. 정답 여부는 사용자 판단 (저장만, 채점 X).

### 옵션화

`init_work`에서 `enable_*` 4개 boolean (기본 모두 True). 비활성 유형은 0개 (재분배 없음). 최소 1개 활성화 필수.

### 챕터 글자 수 기준 문제 최대 개수

| 글자 수 | 객 | 단 | 주 | 확 |
|---|---|---|---|---|
| <3,000 | 3 | 1 | 1 | 1 |
| 3K-10K | 5 | 2 | 2 | 1 |
| 10K-25K | 7 | 3 | 2 | 2 |
| 25K+ | 10 | 4 | 3 | 3 |

표의 숫자는 채워야 하는 목표치가 아니라 **최대 개수**다. 본문 근거가 부족하면
더 적게 만든다. 최대 개수를 맞추기 위해 중복되거나 사소하거나 본문 근거가 약한
문제를 억지로 만들지 않는다.

## user_context 활용

`init_work(user_context="...")`로 받은 학습자 정보를 sub-agent 프롬프트에 주입:

```
[학습자 컨텍스트]
{user_context}

위 학습자 정보를 고려해서 난이도, 표현 수준, 예시를 맞춰주세요.
예: "학부생 대상"이면 너무 어려운 용어 회피, "실무 5년차"면 응용 예시 풍부히.
```

`state.json`에 저장되어 모든 sub-agent가 동일한 컨텍스트 공유.

## Sub-agent 패턴

- **디렉토리 정의 파일 안 만듦** (인라인 방식)
- `get_subagent_prompts`가 시스템 프롬프트 + 워크플로 지시문을 함께 반환
- 메인 LLM이 자기 환경에서 spawn:
  - Claude Code: Task tool의 prompt 인자
  - Codex CLI: /agent 또는 자연어
  - Gemini CLI: 자연어 task delegation
  - 기타 / sub-agent 미지원: 메인 LLM이 직접 순차 처리

### get_subagent_prompts 응답 구조

```python
{
  "ok": True,
  "data": {
    "mode": "sequential" | "parallel",
    "extraction_mode": "text" | "ocr",  # ocr이면 page_images를 읽어 OCR
    "summarizer_prompt": "...",       # 챕터 요약 + 문제 생성 시스템 프롬프트
    "extension_prompt": "...",        # 확장 문제 시스템 프롬프트 (옵션)
    "workflow_instructions": "...",   # 아래 참고
    "chapter_ids": ["ch1", "ch2", ...]
  },
  "next_action": "..."
}
```

### workflow_instructions (모드별)

**sequential**:
```
한 챕터씩 처리하세요.
1) get_chapter_content(chapter_id)로 본문 + 이미지 경로 받기
2) summarizer sub-agent 호출 (없으면 본인이 직접 처리)
3) 결과를 save_chapter_result로 저장
4) enable_extension=True면 extension sub-agent도 동일 처리 → save_extension_result
5) 다음 챕터로 진행
실패 시 1회 재시도.
```

**parallel**:
```
최대 5개 챕터를 동시에 sub-agent로 디스패치하세요.
- 각 sub-agent는 get_chapter_content → 처리 → save_chapter_result까지 완수
- save_*는 서버가 동시성을 보장하므로 결과 도착 순서대로 호출 가능
- 5개 배치 완료 후 다음 5개 시작
- 실패 챕터는 모든 배치 종료 후 1회 재시도
```

### 저장 검증 & 진행 상태

- **필수값 검증(저장 시 거부):** `save_chapter_result`는 `summary`·`key_points`와
  **활성화된** 문제 유형(mc/sa/rf)이 모두 비어있지 않은지, `save_extension_result`는
  `questions.extension`이 비어있지 않은지 확인한다. 하나라도 누락/빈값이면 `ok=False`
  (`data.missing`)로 거부하고 `completed`로 마킹하지 않는다 → **"모두 생성했다"고
  단정하기 전에 각 필드를 직접 확인하고 저장**할 것(누락 시 그 필드만 채워 재호출).
- **진행 상태:** `get_chapter_content` 호출 시 `summary_status`,
  `search_extension_context` 호출 시 `extension_status`가 `in_progress`로 마킹되어,
  병렬 처리 중 어떤 챕터가 진행 중인지 모니터링할 수 있다(저장 성공 시 `completed`).

### 확장형 context와 model_answer 차이

확장형 문제의 `context`는 `search_extension_context`로 찾은 외부 자료를 문제 풀이에
참고할 수 있게 요약한 **참고 맥락**이다. 사용자가 봐도 되는 문제 자료이며 정답이
아니다. `model_answer`는 사용자가 답변을 작성한 뒤 확인하는 **모범답안**이다.
HTML은 `context`를 질문과 답변 입력란 사이에 "참고 맥락"으로 표시하고,
`model_answer`는 "모범답안 보기/접기" 버튼 뒤에 숨긴다.

### 입력 전달 (extraction_mode별)

- **text 모드**: `get_chapter_content`가 `text`(본문)를 준다. sub-agent는 text를 읽고
  요약/문제를 만든다.
- **ocr 모드**: 본문 텍스트가 없다. 대신 `page_images`(챕터 페이지를 렌더한
  JPEG **절대 경로**)를 순서대로 멀티모달로 읽어 **본문을 직접 OCR**한다.
  - 프롬프트(`prompts.py`의 `INPUT_MODE_OCR_*`)가 "page_images를 순서대로 읽어
    본문을 파악하고, 흐릿한 기술용어·식별자·예약어는 문맥으로 복원하라"고 지시.
  - 읽어낸 글자수로 위의 문제 개수 표를 적용 (서버는 글자수를 모름).

> **그림(figure)은 다루지 않는다.** 본문 그림 추출·삽입·렌더링 기능은 제거됐다.
> `page_images`는 그림이 아니라 OCR용 페이지 렌더다.

## 요약 형식 — 마크다운

`summary`는 **마크다운**으로 작성한다(프롬프트의 `SUMMARY_FORMAT_*` 블록이 지시).
렌더 시 HTML은 `markdown-it-py`, TUI는 `rich`로 해석되므로 `##` 소제목·**굵게**·
목록·코드블록·표가 그대로 살아난다. (HtmlRenderer는 요약 본문 헤딩을 섹션 제목
'요약'(h2) 아래로 한 단계 낮춰 계층을 맞춘다.)

본문에 `3.1`, `3.2` 같은 서브 챕터가 있으면 `summary` 안에 각 서브 챕터마다
`## 3.1 ...`, `## 3.2 ...` 섹션을 만든다. 번호가 붙은 서브 챕터가 없을 때만
본문의 실제 소제목이나 의미 단락을 기준으로 섹션을 나눈다. 요약 길이는 본문
글자 수 기반 표로 산정하지 않는다.

이미지(그림)는 넣지 않는다 — 프롬프트가 `![...]()` 이미지 문법 사용을 금지하고,
필요한 그림 내용은 글/표로 풀어 설명하도록 지시한다.

병렬 모드의 동시성 보장은 [06-concurrency.md](./06-concurrency.md) 참고.
