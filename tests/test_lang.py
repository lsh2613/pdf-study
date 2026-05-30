"""lang.detect_language 단위 테스트."""
from __future__ import annotations

from pdf_study import lang


def test_empty_text_falls_back_to_en():
    assert lang.detect_language("") == "en"


def test_pure_korean_paragraph_detected_as_ko():
    text = (
        "한국어 본문이 충분히 길게 들어 있어야 한다. 데이터베이스, 인덱싱, 트랜잭션 "
        "같은 기술 용어가 한글로 등장하는 경우다."
    )
    assert lang.detect_language(text) == "ko"


def test_pure_english_paragraph_detected_as_en():
    text = (
        "This English paragraph is long enough to exceed the minimum character threshold. "
        "Transactions, indexes, and replication are the core topics."
    )
    assert lang.detect_language(text) == "en"


def test_mixed_text_with_some_korean_detected_as_ko():
    # KO_RATIO_THRESHOLD = 0.05 — 라틴 알파벳이 많아도 한글이 5% 이상이면 ko.
    text = (
        "Refactoring is a technique. 리팩터링은 동작을 바꾸지 않으면서 코드를 개선하는 기술이다. "
        "We explore practical examples here in this chapter."
    )
    assert lang.detect_language(text) == "ko"


def test_very_short_input_uses_simple_majority():
    # 최소 글자 수 미만이면 단순 다수결.
    assert lang.detect_language("안녕") == "ko"
    assert lang.detect_language("Hi") == "en"
    # 공백/숫자만은 의미 있는 문자가 0개 → en fallback
    assert lang.detect_language("   123 ") == "en"


def test_predominantly_english_with_one_hangul_char_still_ko():
    """KO 임계 5% 만족 시 ko. 50자 짧은 입력에선 단순 다수결 발동.

    여기선 한글이 0개라 en. 임계 위 케이스는 위의 mixed 테스트에서 확인.
    """
    text = "An English sentence about software engineering practices and design"
    assert lang.detect_language(text) == "en"
