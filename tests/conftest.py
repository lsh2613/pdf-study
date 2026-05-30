"""pytest 공용 설정 + fixture.

- PDF fixture가 없으면 첫 실행 시 자동 빌드 (build_fixtures.build_all).
- ko_with_toc / ko_short / scanned_empty 경로를 fixture로 노출.
- tmp_workspace는 매 테스트에 임시 output_dir + 자동 register 도우미를 제공.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from .fixtures.build_fixtures import FIXTURES_DIR, build_all

_FIXTURE_FILES = ("ko_with_toc.pdf", "ko_short.pdf", "scanned_empty.pdf")


def pytest_configure(config):
    """PDF fixture가 빠져 있으면 즉시 빌드.

    polyfill: 합성 PDF는 git에 들어가지 않으므로 첫 실행/clean 환경에서
    자동으로 생성한다.
    """
    missing = [f for f in _FIXTURE_FILES if not (FIXTURES_DIR / f).exists()]
    if missing:
        build_all()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def ko_with_toc(fixtures_dir) -> Path:
    return fixtures_dir / "ko_with_toc.pdf"


@pytest.fixture(scope="session")
def ko_short(fixtures_dir) -> Path:
    return fixtures_dir / "ko_short.pdf"


@pytest.fixture(scope="session")
def scanned_empty(fixtures_dir) -> Path:
    return fixtures_dir / "scanned_empty.pdf"
