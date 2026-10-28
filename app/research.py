"""Cheap research mode: pull the readable text of a company's homepage."""

from __future__ import annotations

import urllib.request
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skipping += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data):
        if not self._skipping and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def fetch_site_text(url: str, limit: int = 12000) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; SalesAgent/1.0)"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            html = response.read(800_000).decode(charset, "replace")
    except (OSError, ValueError):
        return ""
    return html_to_text(html)[:limit]
