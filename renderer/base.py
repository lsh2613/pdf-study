"""Renderer 추상 베이스."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Renderer(ABC):
    @abstractmethod
    def render(self, work_id: str, output_dir: Path) -> None:
        """워크스페이스의 chapters/{summaries,quiz,extension_quiz}/,
        book_info.json, state.json을 읽어 비어 있는 staging output_dir에 한 세대의
        학습 자료를 생성한다. 최종 폴더 교체는 output_manager가 담당한다."""
