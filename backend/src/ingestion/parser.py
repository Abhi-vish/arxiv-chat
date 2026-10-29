"""Fetched arXiv content (HTML/PDF) -> clean markdown."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from bs4 import BeautifulSoup
from markdownify import markdownify
import pymupdf
import pymupdf4llm

from .arxiv import FetchedContent

MIN_CHARS = 1500  # below this, assume parse failed (scanned PDF, empty page)

# Elements that are page chrome or noise, not paper content.
_DROP = [
    "script", "style", "noscript", "nav", "button",
    ".ltx_page_footer", ".ltx_pagination",
    ".ltx_bibliography",   # references
    ".ltx_authors",        # author block is noisy; metadata API has authors
]


class ParseFailed(Exception):
    """Could not extract usable text."""


@dataclass(frozen=True)
class ParsedPaper:
    markdown: str
    kind: Literal["html", "pdf"]


def parse_content(content: FetchedContent, *, min_chars: int = MIN_CHARS) -> ParsedPaper:
    """Sync + CPU-bound. In async code call via asyncio.to_thread."""
    if content.kind == "html":
        md = _html_to_markdown(content.data)
    elif content.kind == "pdf":
        md = strip_references(_pdf_to_markdown(content.data))
    else:
        raise ParseFailed(f"unsupported kind: {content.kind!r}")

    md = _tidy(md)
    if len(md) < min_chars:
        raise ParseFailed(
            f"only {len(md)} chars extracted (min {min_chars}); "
            "paper may be scanned or empty"
        )
    return ParsedPaper(markdown=md, kind=content.kind)


# HTML

def _html_to_markdown(data: bytes) -> str:
    soup = BeautifulSoup(data, "lxml")

    root = soup.select_one("article.ltx_document")
    drop = list(_DROP)
    if root is None:  # unexpected layout: fall back to body, drop page chrome
        root = soup.body or soup
        drop += ["header", "footer"]

    for el in root.select(", ".join(drop)):
        if not el.decomposed:
            el.decompose()

    # Math -> placeholders. Markdownify would escape _ and * inside LaTeX,
    # so we swap math out first and restore it after conversion.
    store: list[str] = []

    def stash(tex: str, block: bool) -> str:
        tex = " ".join(tex.split())
        store.append(f"$${tex}$$" if block else f"${tex}$")
        return f"ZZMATH{len(store) - 1}ZZ"

    def tex_of(m) -> str:
        return m.get("alttext") or m.get_text()

    # Equation tables: keep the math, drop the table layout and "(1)" numbers.
    for tbl in root.select("table.ltx_equationgroup, table.ltx_equation"):
        if tbl.decomposed or tbl.parent is None:
            continue
        maths = tbl.find_all("math")
        if not maths:
            continue
        p = soup.new_tag("p")
        p.string = " ".join(stash(tex_of(m), True) for m in maths)
        tbl.replace_with(p)

    # Remaining (inline) math.
    for m in root.find_all("math"):
        if m.decomposed or m.parent is None:
            continue
        m.replace_with(stash(tex_of(m), m.get("display") == "block"))

    # Figures: text-only pipeline, keep caption only. (Table figures stay.)
    for fig in root.select("figure.ltx_figure"):
        if fig.decomposed or fig.parent is None:
            continue
        cap = fig.find("figcaption", recursive=False) or fig.find("figcaption")
        if cap is None:
            fig.decompose()
            continue
        p = soup.new_tag("p")
        p.append(cap.extract())
        fig.replace_with(p)

    for el in root.select("img, svg, picture, canvas"):
        if not el.decomposed:
            el.decompose()

    text = markdownify(str(root), heading_style="ATX", strip=["a", "img"])
    return re.sub(r"ZZMATH(\d+)ZZ", lambda m: store[int(m[1])], text)


# PDF

def _pdf_to_markdown(data: bytes) -> str:

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ParseFailed(f"cannot open PDF: {e}") from e
    try:
        return pymupdf4llm.to_markdown(doc)
    except Exception as e:
        raise ParseFailed(f"PDF extraction failed: {e}") from e
    finally:
        doc.close()


_REF_HEADING = re.compile(
    r"^#{1,6}\s*(?:\*\*)?\s*(?:[0-9IVX]+\.?\s*)?(?:references|bibliography)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_APPENDIX_HEADING = re.compile(
    r"^#{1,6}\s*(?:\*\*)?\s*(?:(?i:appendix|appendices|supplementary)|[A-Z][.:]?\s+\S)",
    re.MULTILINE,
)


def strip_references(text: str) -> str:
    """Cut the reference list but keep any appendix after it."""
    matches = list(_REF_HEADING.finditer(text))
    if not matches:
        return text
    m = matches[-1]
    if m.start() < len(text) * 0.3:  # too early: probably not the real list
        return text
    tail = text[m.end():]
    app = _APPENDIX_HEADING.search(tail)
    kept = tail[app.start():] if app else ""
    return (text[: m.start()].rstrip() + "\n\n" + kept).strip()


# shared

def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()