import pytest

from backend.src.ingestion.arxiv import ArxivId, InvalidArxivUrl, parse_arxiv_url


@pytest.mark.parametrize(
    "raw, base, version",
    [
        ("https://arxiv.org/abs/2401.12345", "2401.12345", None),
        ("https://arxiv.org/abs/2401.12345v2", "2401.12345", 2),
        ("https://arxiv.org/pdf/2401.12345", "2401.12345", None),
        ("https://arxiv.org/pdf/2401.12345v3.pdf", "2401.12345", 3),
        ("https://arxiv.org/pdf/2401.12345.pdf", "2401.12345", None),
        ("https://arxiv.org/html/2401.12345v1", "2401.12345", 1),
        ("http://www.arxiv.org/abs/2401.12345", "2401.12345", None),
        ("https://export.arxiv.org/abs/2401.12345", "2401.12345", None),
        ("arxiv.org/abs/2401.12345", "2401.12345", None),
        ("https://arxiv.org/abs/2401.12345?context=cs", "2401.12345", None),
        ("https://arxiv.org/abs/2401.12345#section", "2401.12345", None),
        ("  https://arxiv.org/abs/2401.12345/  ", "2401.12345", None),
        ("2401.12345", "2401.12345", None),
        ("2401.12345v2", "2401.12345", 2),
        ("arXiv:2401.12345v2", "2401.12345", 2),
        ("1706.0376", "1706.0376", None),  # pre-2015: 4-digit sequence
        ("https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001", None),
        ("https://arxiv.org/pdf/hep-th/9901001v2.pdf", "hep-th/9901001", 2),
        ("https://arxiv.org/abs/math.GT/0309136", "math.GT/0309136", None),
        ("cond-mat/0001001", "cond-mat/0001001", None),
    ],
)
def test_valid(raw, base, version):
    assert parse_arxiv_url(raw) == ArxivId(base, version)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "hello",
        "https://example.com/abs/2401.12345",
        "https://notarxiv.org/abs/2401.12345",
        "https://arxiv.org/",
        "https://arxiv.org/list/cs.AI/recent",
        "https://arxiv.org/abs/",
        "https://arxiv.org/abs/2401.123",       # too short
        "https://arxiv.org/abs/2401.123456",    # too long
        "https://arxiv.org/abs/2401.12345v",    # empty version
        "https://arxiv.org/abs/hep-th/99010",   # bad old-style
    ],
)
def test_invalid(raw):
    with pytest.raises(InvalidArxivUrl):
        parse_arxiv_url(raw)


def test_derived_urls_and_cache_key():
    a = parse_arxiv_url("https://arxiv.org/pdf/hep-th/9901001v2.pdf")
    assert a.versioned == "hep-th/9901001v2"
    assert a.cache_key == "hep-th_9901001v2"
    assert a.html_url == "https://arxiv.org/html/hep-th/9901001v2"
    assert a.abs_url == "https://arxiv.org/abs/hep-th/9901001v2"
    assert parse_arxiv_url("2401.12345").pdf_url == "https://arxiv.org/pdf/2401.12345"