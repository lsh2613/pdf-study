# 04. 콘텐츠 생성 (문제 + Sub-agent)

## 검증 문제 4유형

| 유형 | 채점 |
|---|---|
| 객관식 (mc) | 자동 채점 — **맞춘 개수 / 전체 개수**만 표시 (점수 환산 없음) |
| 단답형 (sa) | 모범답안 토글 (자가 확인) |
| 주관식 (rf) | 모범답안 토글 (자가 확인) |
| 확장형 (ex, Exa 검색) | 모범답안 토글 + 출처 링크 |

- 점수 환산/가중치 개념 없음. 객관식은 `2/3 맞음` 형태로만 표시.
- 단답/주관/확장은 사용자가 입력 후 "모범답안 보기" 버튼으로 확인. 정답 여부는 사용자 판단 (저장만, 채점 X).

### 옵션화

`init_work`에서 `enable_*` 4개 boolean (기본 모두 True). 비활성 유형은 0개 (재분배 없음). 최소 1개 활성화 필수.

### 챕터 글자 수 기준 동적 스케일

| 글자 수 | 객 | 단 | 주 | 확 | 합계 |
|---|---|---|---|---|---|
| <3,000 | 3 | 1 | 1 | 1 | 6 |
| 3K-10K | 5 | 2 | 2 | 1 | 10 |
| 10K-25K | 7 | 3 | 2 | 2 | 14 |
| 25K+ | 10 | 4 | 3 | 3 | 20 |

### 요약 길이 권장 (챕터 본문 분량 기반)

고정 한도 대신 **본문 글자수의 약 1/3** 을 기준선으로 sub-agent가 조절.
매우 짧은 챕터는 ~1/2까지, 매우 긴 챕터는 ~1/4 수준으로 완만하게.

| 챕터 본문 글자 수 | 요약 권장 길이 | 비율 |
|---|---|---|
| <2,000 | 800–1,200자 | ≈ 1/2 |
| 2K–10K | 1,000–3,500자 | ≈ 1/3 |
| 10K–25K | 3,000–8,000자 | ≈ 1/3 |
| 25K+ | 6,000–10,000자 | ≈ 1/4 |

권장값이며 강제 아님. 본문 성격(코드 위주·정의 위주·서사형)에 따라 조절.

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

### 이미지 전달

- `get_chapter_content`는 `image_refs`에 **절대 경로**로 이미지 위치를 제공.
- 프롬프트에 "필요 시 다음 이미지를 참조하세요: [path1, path2, ...]" 라인을 명시 → sub-agent(멀티모달 모델)가 직접 로드/참조.
- 캡션 생성은 sub-agent의 자유 판단.

병렬 모드의 동시성 보장은 [06-concurrency.md](./06-concurrency.md) 참고.
