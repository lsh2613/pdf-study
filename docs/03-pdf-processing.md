# 03. PDF 처리

## 추출 모드 (extraction_mode: "text" | "ocr")

`init_work(extraction_mode=...)`로 **시작 시 사용자에게 강제 선택**받는다.
멀쩡한 PDF인지 사전 판별이 불가능하기 때문에(한글 본문은 잘 추출돼 품질이
`high`로 나와도 영문 합자 구간만 깨지는 부분 모지바케가 흔하다), 판별을
포기하고 사용자가 모드를 고른다.

| 모드 | 본문을 얻는 방법 | 적합 | 비용 |
|---|---|---|---|
| `text` | PyMuPDF 텍스트 레이어 추출 | 디지털/전자책 PDF | 빠름·저렴 |
| `ocr` | **비전 LLM(sub-agent)이 페이지 이미지를 직접 읽음** | 스캔본·글꼴 깨진 PDF | 느림·비쌈 |

### OCR 모드 동작 (시스템 의존성 0 유지)

서버에 OCR 엔진을 넣지 않는다. "OCR"은 **이미 멀티모달인 sub-agent가 렌더된
페이지 이미지를 읽는 것**이다(문맥으로 `SERIALIZABLE` 같은 깨진 토큰까지 복원).

- `reader.render_pages(doc, start, end, dir, dpi=150, quality=80)` 가 페이지를
  **JPEG**로 렌더(`.work/raw_data/pages/p{N}.jpg`, 페이지 단위라 scan↔챕터
  재사용·캐시). PyMuPDF `get_pixmap` + PIL만 사용.
- `scan_pdf`: 첫 N페이지만 렌더(`scan_page_images`)해 **목차는 메인 에이전트가
  그 이미지로 직접 분석**한다(`toc_finder` 스크립트 파싱은 OCR 모드에서 돌리지
  않음 — 깨진 텍스트에선 쓰레기 후보만 나와 에이전트를 헷갈리게 하므로
  `toc_candidates`는 빈 후보 + note로 반환). **서버는 챕터를 제안하지 않는다**:
  `recommendations.primary_mode="analyze_toc_from_images"`, `suggested_chapters=[]`,
  균등 청크는 "목차를 못 읽을 때만 쓰는 최후 수단"으로 **`chunk_fallback`에 분리**
  한다(에이전트가 청크를 '추천 챕터'로 오해해 그대로 제시하던 문제 방지). 텍스트
  품질 거부(`no_text_layer`/`garbled`)는 **우회**(스캔본이 대상). 단 offset·언어는
  텍스트 레이어 best-effort로도 시도한다 — 꼬리말 **숫자**와 한글은 합자 깨짐에도
  살아남아 공짜로 쓸 수 있기 때문(없으면 LLM이 이미지로).
- `set_chapters`: 본문 텍스트를 추출하지 않는다(그림 추출도 하지 않는다).
- `get_chapter_content`: 챕터 page_range를 lazy 렌더해 `page_images`로 반환.
- 문제 개수·요약 길이 스케일: sub-agent가 OCR로 읽어낸 글자수로 기존 표를 적용.

## PDF 처리 정책

- **text 모드는 PyMuPDF 텍스트 레이어만 사용, ocr 모드는 페이지 이미지를 LLM이 읽음**
- 정규식으로 기계적 노이즈 1차 정리:
  - `�` (깨진 유니코드) 제거
  - 반복 공백 정규화
  - 페이지 번호만 있는 줄 제거
- OCR 오류 교정은 sub-agent(LLM)가 요약 과정에서 자연 처리
- 텍스트 품질 점수: `high` / `medium` / `low` / `no_text_layer` / `garbled`
- `no_text_layer` (avg <50자/페이지) → text 모드에선 거부 + ocrmypdf/`extraction_mode="ocr"` 안내. **ocr 모드에선 거부하지 않음**
- `garbled` (모지바케; avg_mojibake >0.06) → 거부 + 삼중 안내:
  - 글꼴 ToUnicode 매핑 손상으로 추출 텍스트가 깨진 경우.
  - `recommendations.text_sample`에 깨진 텍스트 일부(최대 600자)를 실어
    사용자가 직접 확인할 수 있게 한다.
  - ① 원본 일부를 추출한 파일이면 무손실 추출(qpdf/pdftk/mutool) 권장,
    ② 원본 자체가 깨졌으면 `ocrmypdf --force-ocr` 권장,
    ③ 샘플 확인 후 강행하려면 `scan_pdf(work_id, allow_garbled=True)` →
    거부를 건너뛰고 깨진 텍스트 그대로 페이지 수 기반 라우팅 진행.
  - 판단: 페이지별 PUA·U+FFFD 비율 + 토큰 내 스크립트 혼합(한글↔라틴/기호)
    경계 비율 + 잡기호 밀집을 합성. 숫자↔쉼표 등 정상 수치 표기는 제외해
    표 데이터 오탐을 방지 (`reader.mojibake_score`).

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
목차엔 전체 책 챕터가 다 적힌 경우) 대응용. 서버의 `_toc_entries_to_chapters`는
시작 물리 페이지가 page_count를 넘는 목차 항목을 page_count로 뭉개지 않고
**드롭**하고, OCR 모드 next_step_guidance도 LLM에 범위 밖 챕터 제외를 지시한다. `next_step_guidance`가 LLM에 두 번호
모두 표기 + 3택(이대로/직접입력(PDF 페이지)/청크) + 경계 의심 시 본문 대조
보정을 지시한다. **set_chapters는 항상 물리 page_range 기준**, printed_range는
표시용 옵셔널 메타다.

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

`scanned_text`(첫 30페이지, OCR 모드는 `scan_page_images`)에는 보통 표지, 판권 페이지, 서문이 포함됨. 메인 LLM이 이걸 보고:
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

## 챕터 분리 모드 (4가지)

`scan_pdf`의 `recommendations` 기반 메인 LLM이 결정:

| 모드 | 트리거 | 동작 |
|---|---|---|
| `from_toc` | 본문에 목차 패턴 발견 | 추출된 챕터 구조 사용 |
| `single_unit` | 짧은 PDF (<50p) 또는 사용자 의도 | chapters=[1개 항목] |
| `chunks` | 50p+ & 목차 없음 | 30페이지 단위 균등 분할 |
| `user_input` | 사용자가 직접 챕터 텍스트/페이지 제공 | LLM이 파싱 후 set_chapters |

### 페이지 수에 따른 자동 추천

| 페이지 수 | 1순위 권장 |
|---|---|
| <50p | `single_unit` |
| 50-200p, 목차 없음 | 사용자에게 질문 (chunks/single 선택) |
| 200p+ 목차 없음 | `chunks` (single은 LLM 부담 경고) |
| 목차 있음 | `from_toc` |

## 본문 목차 감지 (scan_pdf 내부)

키워드: `"목차"`, `"차례"`, `"contents"`, `"table of contents"`

패턴 (4가지 정규식):

```python
TOC_LINE_PATTERNS = [
    re.compile(r'^(.+?)\s*\.{2,}\s*(\d+)\s*$'),         # "제목 .... 12"
    re.compile(r'^(.+?)\s{3,}(\d+)\s*$'),                # "제목    12"
    re.compile(r'^(.+?)\s*[\(\[]\s*(\d+)\s*[\)\]]\s*$'), # "제목 (12)"
    re.compile(r'^(.+?)\t+(\d+)\s*$'),                   # "제목\t12"
]
```

3개 이상 매칭 시 후보로 인정 → 메인 LLM이 검증.
