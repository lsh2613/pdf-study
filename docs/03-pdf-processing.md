# 03. PDF 처리

## PDF 처리 정책

- **PyMuPDF만 사용 (OCR 없음)**
- 정규식으로 기계적 노이즈 1차 정리:
  - `�` (깨진 유니코드) 제거
  - 반복 공백 정규화
  - 페이지 번호만 있는 줄 제거
- OCR 오류 교정은 sub-agent(LLM)가 요약 과정에서 자연 처리
- 텍스트 품질 점수: `high` / `medium` / `low` / `no_text_layer`
- `no_text_layer` (avg <50자/페이지) → 명확히 거부 + ocrmypdf 안내

### 페이지 인덱스 컨벤션

- **외부(LLM/사용자/JSON/HTML)**: 1-based
- **내부(PyMuPDF 호출)**: 0-based
- 변환은 **`pdf/reader.py` 경계에서만** 수행. 다른 모듈은 1-based만 다룬다.
- `page_range: [12, 47]`은 12~47페이지 inclusive (1-based).

### 이미지 추출 필터

`pdf/images.py`가 챕터별로 PNG를 추출하면서 두 가지 필터를 적용한다:

| 필터 | 조건 | 의도 |
|---|---|---|
| 풀페이지 raster 거름 | 페이지 면적의 **70% 이상** 차지 (`page.get_image_info`로 bbox 비율 계산) | 스캔본 PDF의 페이지 전체 raster가 매 페이지마다 잡혀 본문 그림처럼 들어오는 것을 막는다 |
| 작은 이미지 거름 | 긴 변 < 80px | 아이콘·디바이더 같은 비-콘텐츠 |
| 다운스케일 | 긴 변 > 1600px | 멀티모달 sub-agent 입력 부담 감소 |

같은 xref가 여러 페이지에 박혀 있어도 중복 추출하지 않는다.

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

`scanned_text`(첫 20페이지)에는 보통 표지, 판권 페이지, 서문이 포함됨. 메인 LLM이 이걸 보고:
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

`.work/pdf_analysis/book_info.json`으로 저장. `finalize_study`가 index.html 상단에 렌더링.

## 챕터 분리 모드 (4가지)

`scan_pdf`의 `recommendations` 기반 메인 LLM이 결정:

| 모드 | 트리거 | 동작 |
|---|---|---|
| `from_toc` | 본문에 목차 패턴 발견 | 추출된 챕터 구조 사용 |
| `single_unit` | 짧은 PDF (<50p) 또는 사용자 의도 | chapters=[1개 항목] |
| `chunks` | 50p+ & 목차 없음 | 20페이지 단위 균등 분할 |
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
