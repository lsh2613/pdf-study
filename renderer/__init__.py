"""Renderer 인터페이스 + 등록 dispatch."""
from .base import Renderer
from .html_renderer import HtmlRenderer
from .md_tui_renderer import MdTuiRenderer

RENDERERS: dict[str, type[Renderer]] = {
    "html": HtmlRenderer,
    "md_tui": MdTuiRenderer,
}

__all__ = ["Renderer", "HtmlRenderer", "MdTuiRenderer", "RENDERERS"]
