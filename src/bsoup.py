"""HTML 解析：優先 lxml，否則使用內建 html.parser，避免環境未裝 lxml 時整段爬蟲失敗。"""

from __future__ import annotations

from bs4 import BeautifulSoup

_PARSER: str | None = None


def _preferred_parser() -> str:
    global _PARSER
    if _PARSER is None:
        try:
            import lxml  # noqa: F401

            _PARSER = "lxml"
        except ImportError:
            _PARSER = "html.parser"
    return _PARSER


def soup_from_html(markup: str | bytes | None) -> BeautifulSoup:
    if markup is None:
        return BeautifulSoup("", "html.parser")
    if isinstance(markup, bytes):
        text = markup.decode("utf-8", errors="replace")
    else:
        text = str(markup)
    for parser in (_preferred_parser(), "html.parser"):
        try:
            return BeautifulSoup(text, parser)
        except Exception:
            continue
    return BeautifulSoup("", "html.parser")
