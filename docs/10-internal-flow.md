# 10. 내부 동작 흐름

사용자가 MCP 클라이언트(Claude Desktop / Gemini CLI / Codex CLI 등)에서
"이 PDF로 학습 자료 만들어줘"라고 했을 때 `pdf_study` MCP 서버가
어떻게 동작하는지, 어떤 파일·어떤 함수가 호출되는지 단계별로 정리한다.

본문에 등장하는 모든 경로는 패키지 루트(`pdf_study/`) 기준이다.

---

## 큰 그림

```
┌──────────────────────────────────────────────────────────────┐
│ MCP 클라이언트 (메인 LLM = Claude / Gemini / Codex / …)      │
│   사용자: "PDF 학습자료 만들어줘"                            │
└──────────────────────────────────────────────────────────────┘
        │  stdio (JSON-RPC)
        ▼
┌──────────────────────────────────────────────────────────────┐
│ FastMCP 서버 (python -m pdf_study)                           │
│   server.py에 11개 도구 등록                                  │
└──────────────────────────────────────────────────────────────┘
        │ 도구 호출 (init_work → scan_pdf → set_chapters → …)
        ▼
┌──────────────────────────────────────────────────────────────┐
│ 작업 디스크 레이아웃: <output_dir>/.work/                    │
│   state.json / pdf_analysis/ / chapters/ / extensions/ /     │
│   pdf_analysis/chapters_raw/ / pdf_analysis/images/          │
└──────────────────────────────────────────────────────────────┘
        │ finalize_study 후
        ▼
┌──────────────────────────────────────────────────────────────┐
│ 학습 자료 정적 사이트: <output_dir>/                         │
│   index.html / ch{N}.html (또는 main.html) / assets/ /       │
│   images/ / serve.py / progress/                             │
└──────────────────────────────────────────────────────────────┘
```

메인 LLM은 도구를 **순서대로** 호출한다. 각 도구의 응답 `next_action`이
다음 단계를 자연어로 안내한다.

---

## 단계별 호출 흐름

### Stage 0 · 서버 부팅 (사용자가 PDF를 던지기 전)

- 클라이언트(예: Gemini)가 settings.json의 `command`/`args`로
  `python -m pdf_study`를 stdio 자식 프로세스로 spawn한다.
- 진입점: `__main__.py` → `server.main()` → `FastMCP("pdf-study").run()`
- 이 시점에 `server.py` 모듈이 import되며 11개 도구가 mcp 인스턴스에 등록된다.
- 도구 응답은 모두 `_safe` 데코레이터로 감싸여 예외가 `{ok: false, error: ...}`로
  변환된다(`server.py:_safe`, sync/async 모두 지원).

### Stage 1 · `init_work` — 워크스페이스 발급

```
사용자 발화에서 메인 LLM이 추출:
  pdf_path, (옵션) output_dir, execution_mode, enable_*, user_context
        │
        ▼
server.init_work(...)
   - output_dir이 비었으면 default = <Path.cwd()>/result/<pdf_basename>/
     (PDF 파일명에서 안전하지 않은 문자는 `_`로 치환, 같은 PDF 재실행 시 덮어씀)
   - work_id는 분리되어 발급되어 state.json에만 기록됨
        │
        ▼
workspace.create_workspace(..., work_id=work_id)
   - PDF 존재·옵션·execution_mode 검증
   - <output_dir>/.work/ 하위 폴더 생성
   - state.json 초기화 (phases=pending, chapters={})
   - register(work_id → work_dir)  in-memory registry
```

- 코드: `workspace.py:create_workspace`, `make_work_id`, `_validate_options`,
  `register`. server는 `Path.cwd() / "result" / _pdf_name_slug(pdf_path)` 를
  default로 계산 (`_pdf_name_slug`가 영숫자/한글/`_-.` 외 문자를 `_`로 치환).
- 결과: `state.json` 한 파일 + 빈 디렉토리 트리
- 응답 data: `{work_id, work_dir, output_dir}` — `output_dir`은 실제로 사용된
  절대 경로(자동 default든 사용자 지정이든)
- next_action: `scan_pdf(work_id, scan_size=20)`

### Stage 2 · `scan_pdf` — PDF 분석 + 챕터 분리 추천

```
server.scan_pdf(work_id, scan_size)
  └─ analysis.scan_pdf_impl(work_id, scan_size)
       ├─ workspace.update_phase("scanning", "in_progress")
       ├─ reader.get_pdf_info(pdf_path)              # page_count, book metadata
       ├─ reader.open_pdf(pdf_path) → doc
       │    ├─ reader.evaluate_text_quality(doc)     # quality + avg_chars/p
       │    ├─ reader.extract_text_range(doc, 1, N)  # 첫 N페이지 통합 텍스트
       │    └─ doc.close()
       ├─ lang.detect_language(scanned_text)         # ko/en
       ├─ toc_finder.find_toc_candidates(scanned_text)
       │    └─ 4가지 정규식 매칭 + 단조 LIS 필터
       ├─ _build_recommendations(page_count, toc_result, text_quality)
       │    ├─ no_text_layer        → rejected + ocrmypdf 안내
       │    ├─ toc.is_candidate     → from_toc + suggested_chapters
       │    ├─ page_count < 50      → single_unit
       │    ├─ page_count ≥ 200     → chunks (20p 단위)
       │    └─ 그 외                → ask_user (chunks 기본 fallback)
       ├─ workspace.update_state(page_count, text_quality, language)
       ├─ workspace.update_phase("scanning", "completed")
       └─ workspace.save_outline(...)   # .work/pdf_analysis/outline.json
```

- 코드: `analysis.py:scan_pdf_impl`, `_build_recommendations`,
  `_toc_entries_to_chapters`
- 응답: `book_metadata, scanned_text, language, toc_candidates,
  recommendations.{primary_mode, suggested_chapters, alternatives, reason}`
- `rejected=True` 면 서버는 `ok=False`로 변환하여 메인 LLM에게 OCR 안내 전달.

### Stage 3 · `set_chapters` — 챕터 구조 확정 + 추출

메인 LLM이 `recommendations.suggested_chapters`를 그대로 쓸지,
사용자에게 확인할지, `skip: true`(찾아보기·색인·판권)를 추가할지 결정한다.

```
server.set_chapters(work_id, chapters, book_info)
  └─ analysis.set_chapters_impl(work_id, chapters, book_info)
       ├─ _validate_chapter_def(ch, page_count)           # 페이지 범위 검증
       ├─ workspace.set_chapters_in_state(work_id, normalized)
       │    - chapter[skip=True] → status=skipped
       │    - 그 외             → status=pending
       │    - phases.chapter_setup = "completed"
       ├─ workspace.save_book_info(work_id, book_info)    # 없으면 PDF 메타로 fallback
       ├─ workspace.update_phase("chapter_processing", "in_progress")
       └─ for ch in normalized:
            ├─ skip이면 추출 자체 건너뜀 (raw/이미지 파일 없음)
            ├─ chapter_mod.extract_chapter(doc, ch)        # 본문 text+char_count
            ├─ images_mod.extract_chapter_images(doc, ...) # PNG 저장
            │    └─ 페이지 면적 ≥70% raster, <80px 이미지 거름
            ├─ workspace.save_chapter_raw(...)             # chapters_raw/ch{N}.json
            └─ workspace.update_chapter_status(char_count=...)
```

- 코드: `analysis.py:set_chapters_impl`, `pdf/chapter.extract_chapter`,
  `pdf/images.extract_chapter_images`
- 결과 디스크 상태: `chapters_raw/ch{N}.json` (skip 제외), `images/*.png`,
  `book_info.json`
- next_action: `get_subagent_prompts(work_id)`

### Stage 4 · `get_subagent_prompts` — sub-agent용 프롬프트 발급

```
server.get_subagent_prompts(work_id)
  ├─ state = workspace.load_state(work_id)
  ├─ book_info = workspace.load_book_info(work_id)
  └─ prompts.build_prompts(state, book_info)
       ├─ language로 KO/EN 템플릿 선택 (SUMMARIZER_*, EXTENSION_*)
       ├─ user_context, book_info, enabled_types, scales_table 모두 치환
       ├─ execution_mode로 sequential / parallel workflow_instructions 분기
       └─ chapter_ids (skip 제외) + skipped_chapter_ids 분리 반환
```

- 코드: `prompts.py:build_prompts`, `_format_*` 헬퍼
- 응답: `mode, language, summarizer_prompt, extension_prompt,
  workflow_instructions, chapter_ids, skipped_chapter_ids, enabled_types`
- 메인 LLM은 이 시스템 프롬프트를 자기 환경(Task tool / 직접 처리)에
  주입하여 챕터마다 결과 JSON을 생성한다.

### Stage 5 · 챕터 단위 처리 루프

`workflow_instructions`가 sequential인지 parallel인지에 따라 메인 LLM이
다르게 디스패치한다(병렬 실행은 클라이언트 능력에 의존 — Claude Code의
Task tool만 진짜 병렬, Gemini/Codex는 메인 모델이 순차 처리).

챕터당 흐름:

```
(1) get_chapter_content(work_id, chapter_id)
       └─ workspace.get_chapter_raw → text, image_refs(절대 경로) 반환

(2) summarizer sub-agent (메인 LLM이 위 프롬프트로 호출)
       - text + image_refs로 멀티모달 입력
       - 결과 JSON: {summary, key_points, questions:{mc,sa,rf}}

(3) save_chapter_result(work_id, chapter_id, data)
       └─ workspace.save_chapter_result
            - chapters/ch{N}.json 저장 (atomic write)
            - state lock 안에서 summary_status="completed"

(4) (extension 활성 시)
    search_extension_context(work_id, chapter_id, query)
       └─ exa_client.search(query)  ← Exa Web Research MCP HTTP 호출
       - 실패해도 빈 results + ok=True (graceful degrade)

(5) extension sub-agent → save_extension_result
       └─ extensions/ch{N}.json + extension_status="completed"
```

- 코드: `server.py:get_chapter_content/save_chapter_result/
  save_extension_result/search_extension_context`,
  `workspace.py:save_chapter_result/save_extension_result`,
  `exa_client.py:search/_parse_exa_plaintext`
- 동시성 보장: parallel 모드에서 여러 sub-agent 결과가 동시에 들어와도
  `workspace._get_lock(work_id)`이 state.json의 read-modify-write를 직렬화한다.

### Stage 6 · 진행 상황 점검 + 재시도

`list_pending_chapters`는 `completed`와 `skipped`를 모두 처리 완료로 보고,
`pending`/`in_progress`/`failed`만 미처리로 분류한다.

```
server.list_pending_chapters(work_id)
  └─ workspace.list_pending_chapters_impl
       - summary_pending: status not in (completed, skipped)
       - extension_pending: 위와 동일 (option off면 server에서 빈 list 반환)
```

- 코드: `workspace.py:list_pending_chapters_impl`, `_DONE_STATUSES`
- 실패 챕터는 `workspace.mark_chapter_failed`로 retry_count++ 가 누적되어,
  메인 LLM이 1회 재시도 후 포기하기 좋게 신호한다.

### Stage 7 · `finalize_study` — 정적 사이트 렌더링

```
server.finalize_study(work_id, output_format="html", keep_work_dir=True)
  ├─ RENDERERS["html"] = HtmlRenderer
  └─ HtmlRenderer.render(work_id, output_dir)
       ├─ _load_all
       │    - state, book_info, chapters/ch{N}.json,
       │      extensions/ch{N}.json, chapters_raw/ch{N}.json 모두 로드
       │    - state.chapters[*]에서 skip=True 인 챕터는 제외
       ├─ _copy_assets       → style.css, storage.js, grading.js, serve.py, README.md
       ├─ _copy_chapter_images → images_refs를 output/images/ 로 복사
       └─ 멀티 챕터:
            - index.html(책 정보 + 챕터 카드)
            - ch{N}.html (각 챕터, 사이드바·완료 버튼 포함)
          단일 챕터:
            - main.html (책 정보 상단 + 사이드바 없음)
```

- 코드: `renderer/html_renderer.py:HtmlRenderer.render`, `_sidebar`,
  `_chapter_body`, `_page_shell`
- next_action 응답에:
  - `serve_command`: `cd <output_dir> && python3 serve.py`
  - 파일을 file://로 직접 열면 /api/progress가 동작하지 않는다는 경고
  - `Ctrl+C`로 서버 종료, 백그라운드 띄웠을 때의 `pkill` 안내

### Stage 8 · 학습 — `serve.py`

`finalize_study`가 복사한 `templates/html/serve.py`가 정적 파일 + 진도 API를
제공한다.

```
GET  /<file>                      → 정적 서빙 (index.html / chN.html / assets/ /)
GET  /api/progress/global         → progress/_global.json
GET  /api/progress/<chapter_id>   → progress/<chapter_id>.json
POST /api/progress/global         → JSON 검증 후 저장
POST /api/progress/<chapter_id>   → 같은 규칙
```

- 파일명은 `SAFE_NAME = ^[a-zA-Z0-9_-]+$` 검증 (path traversal 방지)
- 잘못된 JSON 본문은 400으로 거부
- 브라우저(storage.js)가 답안/완료 버튼/last_position을 raw fetch로 저장

진도 데이터는 `<output_dir>/progress/_global.json` 과 `<chapter_id>.json`
형태로 디스크에 즉시 떨어진다. 서버를 끄지 않고 브라우저 탭만 닫으면
저장된 내용은 그대로 남는다.

---

## 파일 → 책임 한눈에

| 파일 | 단계 | 책임 |
|---|---|---|
| `__main__.py` | 0 | `python -m pdf_study` 진입점 |
| `server.py` | 0–7 | FastMCP 인스턴스 + 11개 도구. 모든 응답 envelope 보장 |
| `workspace.py` | 1·3·5·6 | `.work/` 폴더 + state.json + work_id별 lock + atomic write |
| `pdf/reader.py` | 2·3 | PyMuPDF 래퍼 (메타·텍스트·품질, 1↔0-based 변환 경계) |
| `pdf/toc_finder.py` | 2 | 본문에서 목차 후보 추출 |
| `pdf/chapter.py` | 2·3 | 청크 분할, 챕터 텍스트 추출 |
| `pdf/images.py` | 3 | 챕터별 이미지 PNG + 풀페이지/소형 필터 |
| `lang.py` | 2 | 한글/라틴 비율로 ko/en 감지 |
| `analysis.py` | 2·3 | scan_pdf_impl / set_chapters_impl 통합 로직 |
| `prompts.py` | 4 | sub-agent KO/EN 템플릿 + workflow + chapter_ids 분리 |
| `exa_client.py` | 5 | Exa Web Research MCP HTTP 호출 + 평문 파서 |
| `renderer/html_renderer.py` | 7 | 정적 사이트 합성 (사이드바·완료 토글·옵션 비활성 섹션 생략) |
| `templates/html/{style.css,storage.js,grading.js,serve.py,README.md}` | 7·8 | 학습 자료 정적 리소스 + 런처 |

---

## 데이터 흐름 (디스크 관점)

```
T0  init_work
    └─ state.json
T1  scan_pdf
    └─ state.json (language, text_quality, page_count 채움)
    └─ pdf_analysis/outline.json
T2  set_chapters
    ├─ state.json (chapters 채움, skip=true는 skipped 상태)
    ├─ pdf_analysis/book_info.json
    ├─ pdf_analysis/chapters_raw/ch{N}.json   (skip 제외)
    └─ pdf_analysis/images/*.png
T3  챕터 루프
    ├─ chapters/ch{N}.json                    (summarizer 결과)
    └─ extensions/ch{N}.json                  (extension 결과)
       + state.json의 status 갱신 (lock 보호 + atomic)
T4  finalize_study (output_format=html)
    └─ <output_dir>/
       ├─ index.html | main.html
       ├─ ch{N}.html
       ├─ images/*.png             (raw에서 복사)
       ├─ assets/{style,storage,grading}
       ├─ serve.py, README.md
       └─ progress/                (빈 상태)
T5  serve.py 실행
    └─ progress/_global.json + ch{N}.json     (사용자 학습 시 누적)
```

`.work/` 폴더는 `finalize_study(keep_work_dir=False)`로 정리할 수 있다.
기본은 보존이라 같은 워크플로를 재실행할 때 캐시처럼 활용 가능하다.

---

## 메인 LLM이 따라야 할 호출 순서 (요약)

```
init_work
  → scan_pdf
  → (rejected이면 사용자에게 OCR 안내, 종료)
  → set_chapters (skip 챕터는 "skip": true로 표시)
  → get_subagent_prompts
  → for each chapter_id (workflow_instructions에 따라 sequential / parallel):
       get_chapter_content
       → summarizer sub-agent
       → save_chapter_result
       → (extension 활성 시)
           search_extension_context
           → extension sub-agent
           → save_extension_result
  → list_pending_chapters → 실패 챕터 1회 재시도
  → finalize_study
  → 사용자에게 serve.py 시작/종료 명령 안내 (next_action 그대로 전달)
```

각 도구의 시그니처는 [02-mcp-api.md](./02-mcp-api.md), 데이터 스키마는
[05-data-schemas.md](./05-data-schemas.md), 동시성 정책은
[06-concurrency.md](./06-concurrency.md) 참고.
