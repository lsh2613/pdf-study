"""본문 언어 감지 휴리스틱.

우선 지원: 'ko', 'en'. 그 외는 'en' fallback.
한글 음절(가-힣) 비율과 라틴 알파벳 비율 비교.
"""
from __future__ import annotations

import re

_HANGUL_RE = re.compile(r"[가-힣]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# 의미 있는 판정을 위한 최소 글자 수
MIN_CHARS_FOR_DETECTION = 50
# 한글이 차지하는 최소 비율 (ko 판정 임계)
KO_RATIO_THRESHOLD = 0.05


def detect_language(text: str) -> str:
    """간단 비율 기반 언어 감지.

    Returns:
        'ko': 한글 비율이 임계 이상
        'en': 그 외 (사실상 fallback 포함)
    """
    if not text:
        return "en"

    # 공백/숫자/기호 제외하여 분모로 쓸 '문자' 카운트
    hangul_count = len(_HANGUL_RE.findall(text))
    latin_count = len(_LATIN_RE.findall(text))
    total_meaningful = hangul_count + latin_count

    if total_meaningful < MIN_CHARS_FOR_DETECTION:
        # 한글이 단 몇 글자라도 더 많으면 ko, 아니면 en
        return "ko" if hangul_count > latin_count else "en"

    ko_ratio = hangul_count / total_meaningful
    if ko_ratio >= KO_RATIO_THRESHOLD:
        return "ko"
    return "en"
