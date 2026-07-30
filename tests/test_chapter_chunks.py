"""chapter.make_chunks 단위 테스트."""
from __future__ import annotations

import pytest

from pdf_learner.pdf import chapter


def test_make_chunks_even_split():
    chs = chapter.make_chunks(100, 20)
    assert len(chs) == 5
    assert chs[0]["pdf_pages"] == [1, 20]
    assert chs[-1]["pdf_pages"] == [81, 100]
    assert [c["chapter_id"] for c in chs] == [f"ch{i}" for i in range(1, 6)]


def test_make_chunks_uneven_tail():
    chs = chapter.make_chunks(45, 20)
    assert len(chs) == 3
    assert chs[-1]["pdf_pages"] == [41, 45]


def test_make_chunks_zero_pages_empty():
    assert chapter.make_chunks(0, 20) == []


def test_make_chunks_chunk_size_one():
    chs = chapter.make_chunks(3, 1)
    assert len(chs) == 3
    assert [c["pdf_pages"] for c in chs] == [[1, 1], [2, 2], [3, 3]]


def test_make_chunks_rejects_zero_chunk_size():
    with pytest.raises(ValueError):
        chapter.make_chunks(10, 0)
