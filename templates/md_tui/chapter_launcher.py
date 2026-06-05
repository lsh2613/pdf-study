#!/usr/bin/env python3
"""이 챕터로 바로 진입하는 launcher.

TUI 엔진은 출력 루트의 study_tui.py에 1벌만 존재하며, 이 파일은 그 엔진을
호출하는 얇은 shim이다 (엔진 중복 없음).
"""
import pathlib
import sys

_here = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_here.parent))

from study_tui import run_chapter  # noqa: E402

run_chapter(_here)
