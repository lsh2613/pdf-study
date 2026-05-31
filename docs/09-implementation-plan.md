# 09. 단계별 구현 계획

## Phase 1: PDF 코어 (2-3시간)

```
1. pdf/reader.py
   - get_pdf_info(pdf_path) → {page_count, book_metadata, ...}
   - extract_page_text(page) + 노이즈 정리
   - evaluate_text_quality(doc) → high/medium/low/no_text_layer
   - 페이지 인덱스 변환은 이 모듈 경계에서만 (외부 1-based ↔ 내부 0-based)
2. pdf/toc_finder.py
   - find_toc_candidates(text) → 후보 목차 항목 리스트
3. pdf/chapter.py
   - make_chunks(page_count, chunk_size) → 청크 fallback
   - extract_chapter(doc, chapter_def) → 챕터 텍스트 추출
4. pdf/images.py
   - extract_chapter_images(doc, page_range, output_dir) → PNG 저장
5. lang.py
   - detect_language(text) → "ko" | "en" | ...
```

**테스트**: 직접 PDF 파일로 함수 호출, 결과 dict 확인.

## Phase 2: 워크스페이스 + 동시성 (1-2시간)

```
6. workspace.py
   - create_workspace(pdf_path, output_dir, options, user_context, execution_mode)
   - work_id 생성: YYYYMMDD-HHMMSS
   - load_state / save_state (atomic write)
   - save_book_info(work_id, book_info)
   - get_chapter_raw / save_chapter_result / save_extension_result
   - update_chapter_status — work_id별 threading.Lock으로 보호
   - list_pending_chapters_impl(work_id)
```

**테스트**:
- 임시 폴더에 워크스페이스 만들고 상태 변화 확인.
- **동시성 테스트**: 여러 스레드에서 동시에 `save_chapter_result` 호출 → `state.json` 무결성 검증.

## Phase 3: 분석 통합 (1-2시간)

```
7. analysis.py
   - scan_pdf_impl(work_id, scan_size)
     - reader.get_pdf_info
     - reader.evaluate_text_quality
     - 첫 N페이지 텍스트 추출
     - lang.detect_language → state에 저장
     - toc_finder.find_toc_candidates
     - build_recommendations(page_count, candidates)
     - 반환: {book_metadata, scanned_text, language, toc_candidates,
              recommendations, ...}
   - set_chapters_impl(work_id, chapters, book_info)
     - 챕터별 텍스트/이미지 추출
     - chapters_raw/ + images/ 저장 (이미지 경로는 절대 경로로도 노출)
     - book_info.json 저장
```

**테스트**: 샘플 PDF로 `scan_pdf` → `set_chapters` 흐름 확인. 한국어/영어 PDF 모두.

## Phase 4: MCP 서버 + 프롬프트 (1-2시간)

```
8. server.py
   - FastMCP 인스턴스 생성
   - 11개 도구 등록 (대부분 thin wrapper, {ok, error, data, next_action} 형식)
   - 도구 description에 워크플로 가이드 포함
   - finalize_study(output_format, keep_work_dir) — Renderer 선택
9. __main__.py
   - mcp.run() 진입점
10. exa_client.py
    - streamablehttp_client로 Exa Web Research MCP 호출 (API key 불필요)
    - search_extension_context 구현
11. prompts.py
    - CHAPTER_SUMMARIZER_TEMPLATE_KO / EN
    - EXTENSION_AGENT_TEMPLATE_KO / EN
    - WORKFLOW_INSTRUCTIONS_SEQUENTIAL / PARALLEL
    - build_prompts(state) — 옵션, user_context, language, question 스케일,
      이미지 참조 지시, 실행 모드별 workflow_instructions 모두 반영
```

**테스트**: Claude Desktop에 등록하고 "이 PDF로 학습 자료 만들어줘" 시도.

## Phase 5: HTML 출력 (2-3시간, 꾸미기 최소)

```
12. templates/html/*
    - (HTML 마크업은 정적 파일이 아니라 html_renderer.py가 f-string으로 합성)
    - style.css: 최소 reset + 본문 가독성 + prefers-color-scheme 다크모드 +
                 사이드바(데스크탑 fixed / 모바일 토글) + 완료 버튼/체크 스타일
    - grading.js: 자리표시(향후 단축키/통계 hook). 채점 자체는 storage.js와 통합.
    - storage.js: fetch로 /api/progress/{id} read/write, 답안 복원,
                  객관식 즉시 채점 + mc_score 갱신, 단답/주관/확장 debounce 저장,
                  "모범답안 보기" 토글, 명시적 "완료" 버튼 토글,
                  IntersectionObserver로 last_position 추적 (자동 진행률 측정 없음)
13. renderer/html_renderer.py
    - book_info.json 읽어서 책 정보 렌더
    - 단일 챕터면 index.html 생략하고 main.html만 (책 정보 main.html 상단에)
    - 옵션 비활성 유형은 섹션 생략
    - <html lang="{language}"> 반영
    - 비활성 유형은 sub-agent가 생성하지 않으므로 자연스럽게 빈 섹션 없음
    - serve.py + README.md 복사
14. renderer/md_tui_renderer.py
    - 인터페이스만 stub. NotImplementedError("ROADMAP")
```

**테스트**: 완성된 폴더 열어서 학습 흐름 검증.
- 자동 포커싱: 서버 재시작 후 마지막 챕터/위치 복원 확인
- 답안 복원: 풀던 문제 그대로 표시되는지 확인
- 다크/라이트 모드 자동 전환 확인

## Phase 6: 통합 테스트 (1시간)

샘플 PDF로 E2E:
- 한국어 native PDF (책 메타 잘 들어있는 것)
- 한국어 스캔본 (OCR 오류 있는 것)
- 챕터 없는 짧은 PDF (PPT 슬라이드 등)
- 큰 PDF (500+ 페이지)

## 테스트 전략

- **단위 테스트**: `pdf/` 모듈 (작은 샘플 PDF로)
- **통합 테스트**: workspace + analysis (임시 폴더)
- **E2E 테스트**: 샘플 PDF로 전체 워크플로 수동 검증
- **회귀 테스트**: `state.json`/`book_info.json` 스키마 변경 시 호환성 확인

## MVP 범위 밖 (ROADMAP)

- 비동기 처리 + 실시간 진행률
- OCR 옵션 재도입 (텍스트 레이어 없는 PDF 지원)
- 모바일 fallback (localStorage)
- 오답노트 자동 생성, Anki 카드 export
- 챕터간 연관 문제, 학습 진도 그래프
- 여러 책 통합 대시보드
- 7-signal급 정교한 챕터 감지 휴리스틱
- 테이블 구조화 추출
- 요약만 만들기 모드
- `output_format="md_tui"` 구현
