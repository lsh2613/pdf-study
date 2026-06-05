# 05. 데이터 스키마 & 폴더 구조

## work_id 규칙

- 형식: `YYYYMMDD-HHMMSS` (예: `20260530-152300`)
- 단순한 타임스탬프. 충돌 가능성은 무시 (같은 초에 두 번 init하지 않음).

## 폴더 구조

### 작업 중 (.work/)

```
.work/
├── state.json
├── pdf_analysis/
│   ├── outline.json
│   ├── book_info.json
│   ├── chapters_raw/ch{N}.json
│   ├── images/*.png          # 추출된 그림(figure)
│   └── pages/p{N}.jpg         # OCR 모드: 페이지를 통째로 렌더한 이미지 (lazy)
├── chapters/ch{N}.json
└── extensions/ch{N}.json
```

### 최종 출력 (study_output/) — output_format="html" 기준

```
study_output/
├── index.html                # 책 정보 섹션 + 챕터 목록 + 진도 대시보드
│                             # (단일 챕터면 생략, main.html이 대체)
├── ch1.html, ch2.html, ...   # 또는 main.html
├── images/*.png
├── assets/
│   ├── style.css             # 최소 스타일 (가독성 + prefers-color-scheme 다크모드)
│   ├── grading.js            # (자리표시 — 채점/입력 hook은 storage.js와 통합)
│   └── storage.js            # progress/ API 호출 + 답안 복원 + 완료 토글 + IntersectionObserver(last_position)
├── progress/                 # 학습 시 자동 생성 (_global.json + ch{N}.json)
├── study_html.py             # launcher (정적 서버 + 진도 API)
└── README.md                 # 사용 안내
```

### 최종 출력 (study_output/) — output_format="md_tui"

챕터별 폴더로 구분된다. 각 챕터 폴더에 요약 md + 문제 데이터 + 진입 launcher가 모두 들어간다.

```
study_output/
├── book.md                   # 책 정보 + 챕터 목차 (각 ch*/summary.md로 링크)
├── study_tui.py              # 학습 TUI 엔진 (rich) — 루트 실행 시 챕터 선택 메뉴
│                             #   rich 없으면 첫 실행 시 자동 설치
├── README.md                 # 실행 안내
├── ch1/
│   ├── summary.md            # 요약 (읽기 전용: 제목·요약·핵심포인트·이미지)
│   ├── quiz.json             # 4유형 문제+정답 (TUI 전용)
│   ├── images/*.png          # 이 챕터 이미지
│   ├── study_tui.py          # 이 챕터로 바로 진입하는 thin launcher → 루트 엔진 호출
│   └── progress.json         # TUI 풀이 기록 (실행 시 생성)
├── ch2/ ...
```

- `summary.md`는 읽기 전용, 문제 풀이는 `quiz.json`을 로드하는 TUI가 담당 (역할 분리).
- `ch*/study_tui.py`는 엔진을 복제하지 않고 루트 `study_tui.py`를 호출하는 얇은 shim이다.
- `progress.json`은 챕터별이며 아래 `progress/ch{N}.json`과 동일 스키마(answers/mc_score/completed)를 따른다 (HTTP 대신 TUI가 파일에 직접 기록).

## 데이터 스키마

### state.json

```json
{
  "work_id": "20260530-152300",
  "pdf_path": "/path/to/book.pdf",
  "output_dir": "/path/to/study_output",
  "started_at": "2026-05-30T15:23:00+09:00",
  "execution_mode": "sequential",
  "extraction_mode": "text",          // "text"(텍스트 추출) | "ocr"(페이지 이미지)
  "language": "ko",
  "question_options": {
    "multiple_choice": true,
    "short_answer": true,
    "reflection": true,
    "extension": true
  },
  "user_context": "데이터베이스 입문서, 학부생 대상",
  "page_count": 487,
  "text_quality": "medium",
  "page_offset": 18,                  // 물리 = 책 + offset, 미측정이면 null
  "page_offset_confidence": "high",   // "high" | "low" | "none"
  "current_phase": "chapter_processing",
  "phases": {
    "scanning": "completed",
    "chapter_setup": "completed",
    "chapter_processing": "in_progress",
    "extension_processing": "in_progress",
    "rendering": "pending"
  },
  "chapters": {
    "ch1": {
      "title": "트랜잭션",
      "page_range": [12, 47],
      "printed_range": [1, 36],
      "char_count": 18420,
      "skip": false,
      "summary_status": "completed",
      "extension_status": "completed",
      "error": null,
      "retry_count": 0
    },
    "ch4": {
      "title": "찾아보기",
      "page_range": [240, 256],
      "char_count": 0,
      "skip": true,
      "summary_status": "skipped",
      "extension_status": "skipped",
      "error": null,
      "retry_count": 0
    }
  }
}
```

- `skip: true` 챕터(찾아보기·색인·판권 등 비본문)는 raw 추출과 sub-agent 디스패치, HTML 렌더링에서 모두 제외된다.
- status 값: `pending` / `in_progress` / `completed` / `failed` / `skipped`. `completed`와 `skipped`는 더 처리할 필요가 없다는 의미로 동일하게 다뤄진다 (`list_pending_chapters`가 둘 다 pending에서 제외).

### book_info.json

```json
{
  "title": "데이터베이스 시스템",
  "author": "홍길동",
  "publisher": "한빛미디어",
  "publication_year": "2025",
  "subject": "데이터베이스 입문",
  "preface_summary": "이 책은 데이터베이스의 기초 개념부터 트랜잭션, 인덱싱, 분산 시스템까지 다루는 입문서다. 학부생과 신입 개발자를 대상으로 하며..."
}
```

### chapters_raw/ch{N}.json (PDF 처리 결과)

```json
{
  "chapter_id": "ch1",
  "title": "트랜잭션",
  "page_range": [12, 47],
  "text": "트랜잭션은 데이터베이스 시스템에서...",   // text 모드만. OCR 모드엔 없음
  "char_count": 18420,                          // OCR 모드는 0 (서버가 본문을 안 읽음)
  "image_refs": [
    {
      "id": "ch1_p23_0",
      "path": "pdf_analysis/images/ch1_p23_0.png",  // 추출된 그림(figure)
      "page": 23
    }
  ]
}
```

- **OCR 모드**: 위에서 `text`가 없고 `char_count=0`. 대신 `get_chapter_content`가
  호출 시 page_range를 lazy 렌더해 응답에 `page_images`를 채운다(디스크 raw엔
  저장 안 함):
  ```json
  "page_images": [
    {"id": "p12", "path": "pdf_analysis/pages/p12.jpg", "page": 12}, ...
  ]
  ```
  sub-agent는 이 이미지를 순서대로 읽어 본문을 OCR한 뒤 요약/문제를 만든다.

### chapters/ch{N}.json (sub-agent 결과)

```json
{
  "chapter_id": "ch1",
  "title": "트랜잭션",
  "summary": "...",
  "key_points": ["...", "..."],
  "questions": {
    "multiple_choice": [
      {
        "id": "mc_1",
        "question": "...",
        "options": ["A", "B", "C", "D"],
        "answer_index": 2,
        "explanation": "..."
      }
    ],
    "short_answer": [...],
    "reflection": [...]
  }
}
```

### extensions/ch{N}.json

```json
{
  "chapter_id": "ch1",
  "questions": {
    "extension": [
      {
        "id": "ex_1",
        "question": "...",
        "context": "Exa로 검색한 외부 자료 요약",
        "model_answer": "...",
        "sources": ["https://...", "..."]
      }
    ]
  }
}
```

### progress/ (학습 시 study_html.py가 생성/관리)

**progress/_global.json** — 전역 상태, 자동 포커싱용
```json
{
  "last_chapter": "ch3",
  "last_position": "section-3-2",
  "last_updated": "2026-05-30T16:00:00+09:00"
}
```

**progress/ch{N}.json** — 챕터별 진도 + 답안 저장
```json
{
  "chapter_id": "ch3",
  "last_position": "section-3-2",
  "completed": true,
  "answers": {
    "mc_1": {"selected": 2, "correct": true},
    "mc_2": {"selected": 0, "correct": false},
    "sa_1": {"text": "사용자가 입력한 답...", "viewed_answer": true},
    "rf_1": {"text": "...", "viewed_answer": false},
    "ex_1": {"text": "...", "viewed_answer": true}
  },
  "mc_score": {"correct": 2, "total": 3},
  "last_updated": "2026-05-30T16:00:00+09:00"
}
```

- `completed`: 사용자가 챕터 끝의 "완료" 버튼을 눌러 명시한 boolean. 자동 측정 없음.
- `last_position`: 마지막으로 보이던 섹션 ID (재진입 시 scroll target)
- `mc_score`: 객관식 정오답 카운트 (점수 환산 X)
- 단답/주관/확장: 사용자 입력 텍스트와 "모범답안 봤는지" 플래그만 저장
