"""테스트용 한국어 PDF fixture 3종 생성 (PyMuPDF + Pillow).

생성:
    1. ko_with_toc.pdf       — 목차 있음, 멀티 챕터, 본문 그림 + 풀페이지 raster + 작은 아이콘
    2. ko_short.pdf          — 짧음(<50p), 목차 없음 → single_unit 권장
    3. scanned_empty.pdf     — 텍스트 레이어 거의 없음 → no_text_layer 거부

사용:
    python -m pdf_study.tests.fixtures.build_fixtures
    또는
    from pdf_study.tests.fixtures.build_fixtures import build_all
    build_all()

생성된 PDF는 git에 들어가지 않습니다(.gitignore). 매번 재생성 가능.
"""
from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image

# macOS 기본 한글 폰트. 다른 OS에선 KO_FONT 환경변수로 오버라이드 가능.
import os
KO_FONT = os.environ.get(
    "PDF_STUDY_KO_FONT",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
)

FIXTURES_DIR = Path(__file__).resolve().parent
ASSETS_DIR = FIXTURES_DIR / "_assets"

PAGE_W, PAGE_H = 595, 841


# ---------------------------------------------------------------------------
# 보조 이미지 생성
# ---------------------------------------------------------------------------

def _ensure_assets() -> dict[str, Path]:
    """배경/그림/아이콘 PNG 캐시. 한 번만 생성."""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    paths = {
        "bg": ASSETS_DIR / "bg_fullpage.png",
        "fig": ASSETS_DIR / "fig_chart.png",
        "icon": ASSETS_DIR / "icon_tiny.png",
        "scan": ASSETS_DIR / "scan_page.png",
    }
    if not paths["bg"].exists():
        Image.new("RGB", (1240, 1754), (245, 244, 240)).save(paths["bg"], "PNG")
    if not paths["fig"].exists():
        # 단순 도식 그림(파란 배경)
        Image.new("RGB", (320, 220), (90, 140, 220)).save(paths["fig"], "PNG")
    if not paths["icon"].exists():
        # 80px 미만 — 이미지 추출에서 걸러져야 함
        Image.new("RGB", (50, 50), (200, 60, 60)).save(paths["icon"], "PNG")
    if not paths["scan"].exists():
        # 스캔본 raster(텍스트가 라스터로 들어간 페이지 시뮬레이션)
        Image.new("RGB", (1240, 1754), (235, 230, 215)).save(paths["scan"], "PNG")
    return paths


# ---------------------------------------------------------------------------
# 텍스트/이미지 페이지 합성
# ---------------------------------------------------------------------------

def _add_text(page: fitz.Page, rect: fitz.Rect, text: str, *, size: int = 11) -> None:
    page.insert_textbox(
        rect, text,
        fontfile=KO_FONT, fontname="kogt", fontsize=size,
        align=fitz.TEXT_ALIGN_LEFT,
    )


def _add_fullpage_bg(page: fitz.Page, bg_path: Path) -> None:
    """페이지 면적 ≥80%를 차지하는 배경 raster (이미지 70% 필터로 걸러져야 함)."""
    page.insert_image(fitz.Rect(0, 30, PAGE_W, PAGE_H - 30), filename=str(bg_path))


def _add_body_figure(page: fitz.Page, fig_path: Path, *, y_top: float = 540) -> None:
    """본문 그림 — 페이지 면적의 약 15% (필터 통과해야 함)."""
    page.insert_image(fitz.Rect(120, y_top, 360, y_top + 160), filename=str(fig_path))


def _add_tiny_icon(page: fitz.Page, icon_path: Path) -> None:
    """80px 미만 아이콘 — 이미지 추출에서 걸러져야 함."""
    page.insert_image(fitz.Rect(500, 60, 540, 100), filename=str(icon_path))


# ---------------------------------------------------------------------------
# PDF 1: ko_with_toc.pdf
# ---------------------------------------------------------------------------

def build_ko_with_toc(out_path: Path) -> Path:
    """한국어 + 목차 + 멀티 챕터 + 본문 그림 + 풀페이지 raster + 작은 아이콘.

    구성 (총 28페이지):
        p.1     표지
        p.2     판권 / 저자 소개
        p.3-4   목차 (CONTENTS — 정규식 매치되는 형식)
        p.5-12  제1장 트랜잭션 (8p, 그림 1장 + 아이콘 1장)
        p.13-20 제2장 인덱싱 (8p, 그림 1장)
        p.21-28 제3장 분산 시스템 (8p, 그림 1장)

    검증 포인트:
        - 내장 목차(set_toc, 물리 p.5/13/21) → recommendations.primary_mode = "from_outline"
        - 멀티 챕터 finalize → index.html + ch{1,2,3}.html + 사이드바
        - 이미지: 풀페이지 배경/작은 아이콘은 본문 렌더에 그대로 둠
    """
    assets = _ensure_assets()
    doc = fitz.open()
    doc.set_metadata({
        "title": "테스트용 한국어 책",
        "author": "테스트 저자",
        "subject": "데이터베이스 시스템 개론",
        "creator": "pdf_study fixture",
        "producer": "PyMuPDF",
    })

    # p.1 표지 (풀페이지 배경)
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _add_fullpage_bg(p, assets["bg"])
    _add_text(p, fitz.Rect(50, 200, 545, 320),
              "테스트용 한국어 책\n\n데이터베이스 시스템 개론",
              size=22)
    _add_text(p, fitz.Rect(50, 500, 545, 600),
              "테스트 저자 지음\n샘플 출판사", size=14)

    # p.2 판권/저자
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _add_fullpage_bg(p, assets["bg"])
    _add_text(p, fitz.Rect(50, 80, 545, 760),
              "지은이 테스트 저자\n"
              "데이터베이스와 분산 시스템 분야 연구자. 다수의 강의와 저서를 통해 "
              "실무 개발자들에게 이론적 토대를 제공한다.\n\n"
              "옮긴이의 글\n이 책은 데이터베이스의 기초부터 분산 시스템까지의 핵심 개념을 "
              "체계적으로 정리한 입문서다. 자동화된 테스트 환경의 한국어 처리 검증을 위해 합성된 샘플이다.")

    # p.3-4 목차 — 우리 정규식 r'^(.+?)\s*\.{2,}\s*(\d+)\s*$' 와 매치되는 형식
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _add_fullpage_bg(p, assets["bg"])
    _add_text(p, fitz.Rect(50, 80, 545, 760),
              "목차\n\n"
              "제1장 트랜잭션 ............ 5\n"
              "제2장 인덱싱 .............. 13\n"
              "제3장 분산 시스템 ......... 21\n"
              "찾아보기 ................... 27\n",
              size=13)
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _add_fullpage_bg(p, assets["bg"])
    _add_text(p, fitz.Rect(50, 80, 545, 760),
              "CONTENTS\n\n(이어지는 목차 페이지)\n", size=13)

    # p.5-12 제1장 (8페이지)
    ch1_body = (
        "트랜잭션은 데이터베이스에 대한 작업의 논리적 단위로, 모두 성공하거나 모두 실패해야 한다는 "
        "전부 또는 전무 원칙을 따른다. 이러한 성질은 흔히 ACID라는 약어로 정리된다. "
        "원자성은 트랜잭션의 모든 연산이 하나의 단위처럼 다뤄짐을 뜻하며, 일관성은 트랜잭션 전후로 "
        "데이터베이스가 정의된 무결성 제약을 만족함을 의미한다. 격리성은 동시에 진행되는 여러 "
        "트랜잭션들이 서로의 중간 상태를 보지 못하도록 보장하고, 지속성은 한 번 커밋된 변경이 "
        "장애 상황에서도 보존됨을 보장한다.\n\n"
        "격리 수준은 직렬화 가능, 반복 가능 읽기, 커밋된 읽기, 커밋되지 않은 읽기로 나뉜다. "
        "직렬화 가능은 가장 엄격한 수준으로 트랜잭션 순서가 직렬 실행과 동등하다.\n\n"
    ) * 2
    for i in range(5, 13):
        p = doc.new_page(width=PAGE_W, height=PAGE_H)
        _add_fullpage_bg(p, assets["bg"])
        _add_text(p, fitz.Rect(50, 80, 545, 700), ch1_body)
        if i == 7:  # 본문 그림 1장
            _add_body_figure(p, assets["fig"])
        if i == 8:  # 작은 아이콘 (걸러져야 함)
            _add_tiny_icon(p, assets["icon"])
        _add_text(p, fitz.Rect(50, 800, 545, 830), f"{i}", size=9)  # 페이지 번호

    # p.13-20 제2장
    ch2_body = (
        "인덱스는 테이블의 특정 열에 대해 빠른 조회를 가능하게 하는 보조 자료 구조다. "
        "대표적인 형태로 B-트리와 해시 인덱스가 있다. B-트리 인덱스는 범위 조회에 강점이 있으며, "
        "해시 인덱스는 정확 일치 조회에 적합하다. 인덱스가 쓰기 비용을 약간 늘리는 대가로 읽기 비용을 "
        "크게 줄여주므로, 시스템의 워크로드 특성에 맞춰 신중하게 설계해야 한다.\n\n"
        "결합 인덱스는 두 개 이상의 열을 함께 인덱싱하며, 좌측 접두사 규칙에 따라 어떤 질의가 "
        "이 인덱스의 혜택을 받을 수 있는지 결정된다.\n\n"
    ) * 2
    for i in range(13, 21):
        p = doc.new_page(width=PAGE_W, height=PAGE_H)
        _add_fullpage_bg(p, assets["bg"])
        _add_text(p, fitz.Rect(50, 80, 545, 700), ch2_body)
        if i == 15:
            _add_body_figure(p, assets["fig"])
        _add_text(p, fitz.Rect(50, 800, 545, 830), f"{i}", size=9)

    # p.21-28 제3장
    ch3_body = (
        "분산 시스템은 네트워크로 연결된 다수의 노드가 협력해 하나의 일관된 서비스를 "
        "제공하는 형태를 말한다. 핵심 과제는 부분 장애, 지연, 네트워크 분리 상황에서도 "
        "정합성을 유지하는 데에 있다. CAP 정리는 일관성, 가용성, 분리 내성 가운데 동시에 "
        "보장 가능한 두 가지를 선택해야 함을 보여준다. 대부분의 실용 시스템은 분리 내성을 전제로 "
        "일관성과 가용성 사이의 적절한 균형점을 찾는다.\n\n"
        "합의 알고리즘으로는 Paxos와 Raft가 널리 알려져 있으며, 두 알고리즘 모두 다수결을 통해 "
        "리더를 선출하고 로그 복제의 일관성을 유지한다.\n\n"
    ) * 2
    for i in range(21, 29):
        p = doc.new_page(width=PAGE_W, height=PAGE_H)
        _add_fullpage_bg(p, assets["bg"])
        _add_text(p, fitz.Rect(50, 80, 545, 700), ch3_body)
        if i == 24:
            _add_body_figure(p, assets["fig"])
        _add_text(p, fitz.Rect(50, 800, 545, 830), f"{i}", size=9)

    # 내장 목차(북마크) — 챕터 경계 1순위 소스. [level, title, 물리 page].
    # 챕터는 물리 p.5/13/21에서 시작 → scan_pdf가 from_outline으로 추천한다.
    doc.set_toc([
        [1, "제1장 트랜잭션", 5],
        [1, "제2장 인덱싱", 13],
        [1, "제3장 분산 시스템", 21],
    ])

    doc.save(str(out_path), garbage=4, deflate=True, clean=True)
    doc.close()
    return out_path


# ---------------------------------------------------------------------------
# PDF 2: ko_short.pdf
# ---------------------------------------------------------------------------

def build_ko_short(out_path: Path) -> Path:
    """짧은 한국어 PDF (12p), 목차 없음, 본문 그림 1장.

    검증 포인트:
        - page_count < 50
        - toc_candidates.is_candidate = False
        - recommendations.primary_mode = "single_unit"
        - 단일 챕터 finalize → main.html (사이드바 없음)
    """
    assets = _ensure_assets()
    doc = fitz.open()
    doc.set_metadata({
        "title": "짧은 한국어 입문서",
        "author": "샘플 저자",
        "subject": "리팩터링 입문",
        "creator": "pdf_study fixture",
        "producer": "PyMuPDF",
    })

    body = (
        "리팩터링은 겉으로 드러나는 동작은 그대로 둔 채 코드의 내부 구조를 더 이해하기 쉽게 "
        "바꾸는 작업이다. 작은 단계로 나누어 컴파일과 테스트를 반복하면, 큰 변경도 안전하게 "
        "수행할 수 있다. 자주 등장하는 기법으로는 함수 추출, 변수 이름 바꾸기, 임시 변수를 "
        "질의 함수로 바꾸기, 단계 쪼개기 등이 있다. 각 기법은 카탈로그에 절차와 함께 정리되어 "
        "있어 필요할 때 안전하게 적용할 수 있다.\n\n"
        "리팩터링이 필요한 신호는 의외로 단순하다. 코드를 읽는데 의도가 한눈에 보이지 않거나, "
        "수정하려는데 어디를 건드려야 할지 명확하지 않다면 그것이 곧 리팩터링이 필요하다는 "
        "신호다.\n\n"
    ) * 2
    for i in range(1, 13):
        p = doc.new_page(width=PAGE_W, height=PAGE_H)
        _add_text(p, fitz.Rect(50, 60, 545, 720), body)
        if i == 5:
            _add_body_figure(p, assets["fig"], y_top=550)
        _add_text(p, fitz.Rect(50, 800, 545, 830), f"- {i} -", size=9)

    doc.save(str(out_path), garbage=4, deflate=True, clean=True)
    doc.close()
    return out_path


# ---------------------------------------------------------------------------
# PDF 3: scanned_empty.pdf
# ---------------------------------------------------------------------------

def build_scanned_empty(out_path: Path) -> Path:
    """텍스트 레이어가 거의 없는 PDF (스캔본 시뮬레이션).

    각 페이지가 배경 raster 한 장만 가짐. 추출되는 텍스트는 거의 없어
    avg <50자/p → text_quality="no_text_layer" → rejected.

    이미지 측면: 페이지 전체 raster만 있으므로 이미지 추출은 0건이어야 한다
    (FULL_PAGE_AREA_RATIO 필터로 모두 걸러짐).
    """
    assets = _ensure_assets()
    doc = fitz.open()
    doc.set_metadata({
        "title": "스캔본 샘플",
        "author": "",
        "creator": "pdf_study fixture",
        "producer": "PyMuPDF",
    })
    for _ in range(5):
        p = doc.new_page(width=PAGE_W, height=PAGE_H)
        # 페이지 전체를 raster 한 장으로 — 텍스트 레이어 없음
        p.insert_image(p.rect, filename=str(assets["scan"]))
    doc.save(str(out_path), garbage=4, deflate=True, clean=True)
    doc.close()
    return out_path


# ---------------------------------------------------------------------------
# 엔트리
# ---------------------------------------------------------------------------

def build_all(out_dir: Path | None = None) -> dict[str, Path]:
    out_dir = out_dir or FIXTURES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "ko_with_toc": build_ko_with_toc(out_dir / "ko_with_toc.pdf"),
        "ko_short": build_ko_short(out_dir / "ko_short.pdf"),
        "scanned_empty": build_scanned_empty(out_dir / "scanned_empty.pdf"),
    }
    return paths


if __name__ == "__main__":
    for name, p in build_all().items():
        size_kb = p.stat().st_size / 1024
        print(f"  {name:18s} {p}  ({size_kb:.1f} KB)")
