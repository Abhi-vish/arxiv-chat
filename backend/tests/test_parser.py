import pytest

from backend.src.ingestion.arxiv import FetchedContent
from backend.src.ingestion.parser import ParseFailed, parse_content, strip_references

HTML = b"""<html><body>
<header>NAVBAR</header>
<article class="ltx_document">
  <h1 class="ltx_title ltx_title_document">My Paper</h1>
  <div class="ltx_authors">Secret Author Block</div>
  <div class="ltx_abstract"><p>We study <math alttext="x_i" display="inline"><mi>x</mi></math> here.</p></div>
  <section class="ltx_section">
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Introduction</h2>
    <p>Prior work <cite class="ltx_cite"><a href="#bib.bib1">[1]</a></cite> exists.</p>
    <table class="ltx_equation"><tr>
      <td><math alttext="E = m c^{2}" display="block"><mi>E</mi></math></td>
      <td class="ltx_eqn_eqno"><span class="ltx_tag ltx_tag_equation">(7)</span></td>
    </tr></table>
    <figure class="ltx_figure"><img src="cat.png"/><figcaption>Figure 1: A cat.</figcaption></figure>
  </section>
  <section class="ltx_bibliography"><h2>References</h2><p>Smith 2020</p></section>
</article>
<footer>FOOTER</footer>
</body></html>"""


def html(data: bytes = HTML) -> FetchedContent:
    return FetchedContent("html", data, "https://arxiv.org/html/x")


def test_html_keeps_content_and_math():
    md = parse_content(html(), min_chars=0).markdown
    assert "# My Paper" in md
    assert "Introduction" in md
    assert "$x_i$" in md                # underscore not escaped
    assert "$$E = m c^{2}$$" in md      # display math kept
    assert "Figure 1: A cat." in md     # caption kept


def test_html_drops_noise():
    md = parse_content(html(), min_chars=0).markdown
    for junk in ["NAVBAR", "FOOTER", "Smith 2020", "Secret Author", "cat.png", "(7)"]:
        assert junk not in md


def test_too_short_raises():
    with pytest.raises(ParseFailed):
        parse_content(html())  # default MIN_CHARS


def test_unsupported_kind():
    with pytest.raises(ParseFailed):
        parse_content(FetchedContent("docx", b"", "u"))  # type: ignore[arg-type]


# ---- PDF ----

def test_bad_pdf_bytes():
    pytest.importorskip("pymupdf4llm")
    with pytest.raises(ParseFailed):
        parse_content(FetchedContent("pdf", b"not a pdf", "u"), min_chars=0)


def test_blank_pdf_fails_validation():
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("pymupdf4llm")
    doc = pymupdf.open()
    doc.new_page()
    with pytest.raises(ParseFailed):
        parse_content(FetchedContent("pdf", doc.tobytes(), "u"))


def test_text_pdf_roundtrip():
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("pymupdf4llm")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Hello arXiv world")
    got = parse_content(FetchedContent("pdf", doc.tobytes(), "u"), min_chars=0)
    assert "Hello arXiv world" in got.markdown


# ---- reference stripping ----

BODY = "Intro text.\n\n" * 20

def test_strip_refs_drops_tail():
    text = BODY + "## References\n\n[1] Smith 2020\n[2] Doe 2021\n"
    out = strip_references(text)
    assert "Smith" not in out and "Intro text." in out


def test_strip_refs_keeps_appendix():
    text = BODY + "## References\n\n[1] Smith\n\n## Appendix A Proofs\n\nProof here.\n"
    out = strip_references(text)
    assert "Smith" not in out
    assert "Proof here." in out


def test_strip_refs_numbered_and_bold():
    text = BODY + "## **7. References**\n\n[1] Smith\n"
    assert "Smith" not in strip_references(text)


def test_strip_refs_noop_when_absent_or_too_early():
    assert strip_references(BODY) == BODY
    early = "## References\n\nfoo\n\n" + BODY * 3
    assert strip_references(early) == early