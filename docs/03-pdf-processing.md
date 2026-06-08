# 03. PDF 처리

## 두 가지 결정의 분리 — 목차(항상 vision) vs 본문(text/ocr 선택)

**목차/챕터 경계 결정은 텍스트 레이어를 신뢰하지 않는다**(스캔본·부분 모지바케에서
정렬이 깨져 잘못된 페이지가 나옴). 그래서 두 결정을 분리한다:

- **목차 단계(`scan_pdf`)** — 모드와 무관하게 항상 (1) 내장 목차(북마크) 또는
  (2) 목차 페이지 이미지(vision)로만 챕터 경계를 정한다.
- **본문 단계(`set_chapters`에서 `extraction_mode` 선택)** — 챕터 본문을 어떻게
  읽을지만 사용자에게 묻는다.

### 본문 추출 모드 (set_chapters의 extraction_mode: "text" | "ocr")

`set_chapters(extraction_mode=...)`로 **목차 확정 후 사용자에게 선택**받는다
(`execution_mode`(순차/병렬)도 함께). 둘 중 하나라도 미지정이면 4조합 `choices`로
거부된다.

| 모드 | 본문을 얻는 방법 | 적합 | 비용 |
|---|---|---|---|
| `text` | PyMuPDF 텍스트 레이어 추출 | 디지털/전자책 PDF | 빠름·저렴 |
| `ocr` | **비전 LLM(sub-agent)이 페이지 이미지를 직접 읽음** | 스캔본·글꼴 깨진 PDF | 느림·비쌈 |

### "OCR"은 sub-agent의 vision (시스템 의존성 0 유지)

서버에 OCR 엔진을 넣지 않는다(tesseract 등 없음). "OCR"은 **이미 멀티모달인
sub-agent가 렌더된 페이지 이미지를 읽는 것**이다(문맥으로 깨진 토큰까지 복원).
MCP의 역할은 **PDF 페이지를 JPEG로 래스터화**하는 것까지다.

- `reader.render_pages(doc, start, end, dir, dpi=150, quality=80)` 가 페이지를
  **JPEG**로 렌더(`.work/raw_data/pages/p{N}.jpg`, 페이지 단위라 scan↔챕터
  재사용·캐시). PyMuPDF `get_pixmap` + PIL만 사용.
- `scan_pdf`(목차): 내장 목차가 없으면 목차 페이지만 렌더(`toc_page_images`)해
  **메인 에이전트가 그 이미지로 직접 분석**한다. 텍스트/스크립트로 목차를 추정하지
  않는다. **서버는 챕터를 제안하지 않는다**: `primary_mode="analyze_toc_from_images"`,
  `suggested_chapters=[]`, 균등 청크는 "목차를 못 읽을 때만 쓰는 최후 수단"으로
  **`chunk_fallback`에 분리**한다. offset·언어는 텍스트 레이어 best-effort로도
  시도하되(없으면 LLM이 이미지로) **`scanned_text`는 노출하지 않는다**.
- `set_chapters`(extraction_mode="ocr"): 본문 텍스트를 추출하지 않는다(그림도 없음).
- `get_chapter_content`: 챕터 page_range를 lazy 렌더해 `page_images`로 반환.
- 문제 개수·요약 길이 스케일: sub-agent가 OCR로 읽어낸 글자수로 기존 표를 적용.

## PDF 처리 정책

- **text 모드는 PyMuPDF 텍스트 레이어만 사용, ocr 모드는 페이지 이미지를 LLM이 읽음**
- 정규식으로 기계적 노이즈 1차 정리:
  - `�` (깨진 유니코드) 제거
  - 반복 공백 정규화
  - 페이지 번호만 있는 줄 제거
- OCR 오류 교정은 sub-agent(LLM)가 요약 과정에서 자연 처리
- 텍스트 품질 점수(`high`/`medium`/`low`/`no_text_layer`/`garbled`)는 `reader`가
  여전히 계산하지만 **챕터 분리 라우팅·거부에는 쓰지 않는다**(정보용). 텍스트
  레이어가 없거나 깨져도 거부하지 않고 **목차 vision 경로**로 간다 — 글자 밀도·
  모지바케율은 "글자가 읽히는가"를 잴 뿐 "배치 순서가 맞는가"(목차 신뢰의 핵심)는
  못 재기 때문. 그래서 목차는 내장 목차/이미지로만 정한다.

### 페이지 인덱스 컨벤션

- **외부(LLM/사용자/JSON/HTML)**: 1-based
- **내부(PyMuPDF 호출)**: 0-based
- 변환은 **`pdf/reader.py` 경계에서만** 수행. 다른 모듈은 1-based만 다룬다.
- `page_range: [12, 47]`은 12~47페이지 inclusive (1-based).

### 페이지 오프셋 (인쇄 책 번호 ↔ PDF 물리 인덱스)

책에 인쇄된 페이지번호와 PDF 물리 인덱스는 보통 다르다(표지·서문 때문).
`reader.detect_page_offset`이 **꼬리말 인쇄번호 다수결**로 `offset`(물리 = 책 + offset)을 측정한다.

- 각 페이지 raw 텍스트의 끝 3줄에서 숫자-only 줄을 인쇄번호로 보고
  `candidate = 물리 − 인쇄`를 모아 최빈값을 취한다.
  (raw를 써야 함 — `extract_page_text`는 숫자-only 줄을 이미 제거한다.)
- **빈 페이지·번호 없는 표지/도입부는 후보에서 자동 제외**, 코드 줄번호 등
  단발 노이즈는 최빈에 밀린다.
- `offset`은 **음수 가능**(PDF가 앞 front matter를 일부 누락한 경우).
- confidence: 최빈 지지 ≥3 & 2등의 2배+ → `high`, 측정됐으나 약하면 `low`,
  인쇄번호 신호 없음(이미지/무텍스트 PDF 등) → `offset=None`/`none`.
- 실측: MySQL +18, 리팩터링 2판 +1(빈 페이지 7장 무시), 데이터베이스 개론
  3판은 텍스트 레이어 없음→`none`(OCR 필요).

오프셋은 `state.json`·`outline.json`·`recommendations`에 실리고, 각
suggested_chapter에 `page_range`(PDF 물리)와 `printed_range`(책, 물리−offset,
front matter면 None)가 함께 담긴다. recommendations에는 `physical_range`
([1, page_count])와 `printed_range_available`([1, page_count−offset], 이 파일에
실제 존재하는 책 페이지 범위)도 실린다 — **발췌본**(책 일부만 담긴 PDF인데
목차엔 전체 책 챕터가 다 적힌 경우) 대응용. 서버의 `_outline_to_chapters`는
시작 물리 페이지가 page_count를 넘는 목차 항목을 page_count로 뭉개지 않고
**드롭**하고, vision 경로 next_step_guidance도 LLM에 범위 밖 챕터 제외를 지시한다.
`next_step_guidance`가 LLM에 두 번호 모두 표기 + MCP `user_choices`를 그대로 제시
(from_outline은 4택: 이대로/vision재분석/직접입력/청크, vision은 3택)하도록 지시한다.
**set_chapters는 항상 물리 page_range 기준**, printed_range는 표시용 옵셔널 메타다.

### 그림(figure) 추출 — 제거됨

본문 그림(figure) 추출 기능은 제거됐다. 요약은 순수 텍스트/마크다운만 다루며,
학습 자료에 별도 그림을 싣지 않는다. (OCR 모드의 `page_images`는 그림이 아니라
sub-agent가 본문을 읽는 페이지 렌더 — `reader.render_pages` — 로 그대로 유지된다.)

### 언어 감지

- `scan_pdf` 내부에서 첫 N페이지 텍스트로 본문 언어를 감지.
- 휴리스틱: 한글 음절(`가-힣`) 비율과 라틴 알파벳 비율 비교.
- 우선 지원: `ko`, `en`. 그 외는 `en` fallback.
- `state.json`에 `"language": "ko"` 저장 → `prompts.py`가 언어별 템플릿 선택, renderer가 `<html lang>` 반영.

## 책 메타 정보 처리

### scan_pdf 응답에 book_metadata 포함

```python
# PyMuPDF의 doc.metadata 활용
{
  "title": "...",          # PDF 내장 메타
  "author": "...",
  "subject": "...",
  "creator": "Adobe InDesign",
  "producer": "..."
}
```

### 메인 LLM이 보완

목차 페이지 이미지(`toc_page_images`)나 내장 목차 + 앞부분 페이지에는 보통 표지,
판권 페이지, 서문이 포함됨. 메인 LLM이 이걸 보고:
- PDF 메타가 비어있거나 부정확하면 본문에서 추출하여 보정
- 서문 내용을 200-400자로 요약
- 출판사, 출판년도 등 추가 정보 파악

### set_chapters에 book_info 전달

```python
set_chapters(
    work_id,
    chapters=[...],
    book_info={
        "title": "데이터베이스 시스템",
        "author": "홍길동",
        "publisher": "한빛미디어",        # 선택
        "publication_year": "2025",       # 선택
        "preface_summary": "이 책은 데이터베이스의 기초 개념부터..."  # 선택, 200-400자
    }
)
```

book_info가 없으면 PDF 메타만 사용.

### 워크스페이스 저장

`.work/raw_data/book_info.json`으로 저장. `finalize_study`가 index.html 상단에 렌더링.

## 챕터 경계 소스 (2가지)

`scan_pdf`의 `recommendations.primary_mode`로 표시된다:

| primary_mode | 트리거 | 동작 |
|---|---|---|
| `from_outline` | 내장 목차(`doc.get_toc`) 있음 | 북마크의 물리 page_range로 챕터 구성. 사용자 확인 후 set_chapters. 틀리면 `scan_pdf(force_vision=True)` |
| `analyze_toc_from_images` | 내장 목차 없음 / `force_vision=True` | `toc_page_images`를 vision으로 직독해 from_toc를 직접 구성. `suggested_chapters=[]`, `chunk_fallback`은 최후 수단 |

`single_unit`/`chunks`는 더 이상 페이지 수로 자동 라우팅하지 않는다 — 에이전트가
목차(또는 이미지)를 보고 적절한 챕터 수를 정하며, 목차를 못 읽을 때만
`chunk_fallback`(균등 분할)이나 단일 챕터로 폴백한다. 사용자가 직접 페이지 범위를
주는 경우(직접 입력)는 `user_choices`의 `manual_pdf_pages`로 받는다.

## 내장 목차 우선 (scan_pdf 내부)

1. `reader.get_outline(doc)` — `doc.get_toc()`로 북마크 트리를 읽는다. 북마크는
   **물리 페이지를 직접** 가리켜 offset 보정·OCR 없이 정확하다. 최상위 레벨 항목만
   챕터로 삼고(`_outline_to_chapters`), 발췌본이면 page_count 초과 항목은 드롭한다.
2. 없으면 `reader.locate_toc_pages(doc)` — `"목차"`·`"차례"`·`"contents"`·
   `"table of contents"` 키워드로 **목차 페이지 위치만** 찾는다(숫자는 신뢰 안 함).
   못 찾으면 앞 N페이지로 폴백. 해당 페이지를 `render_pages`로 JPEG 렌더해
   `toc_page_images`로 준다 → 에이전트가 vision으로 챕터↔페이지를 직독한다.

> 텍스트 기반 목차 정규식 파싱(`toc_finder`)은 챕터 경계 결정에 **더 이상 쓰지
> 않는다** — 스캔본·부분 모지바케에서 제목↔페이지번호 정렬이 깨져 잘못된 챕터가
> 나오던 원인이었다. (모듈은 남아 있으나 메인 흐름에서 호출하지 않는다.)
