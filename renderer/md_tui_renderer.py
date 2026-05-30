"""Markdown + TUI 렌더러 — ROADMAP."""
from __future__ import annotations

from pathlib import Path

from .base import Renderer


class MdTuiRenderer(Renderer):
    def render(self, work_id: str, output_dir: Path) -> None:
        raise NotImplementedError("md_tui renderer is on the ROADMAP")
