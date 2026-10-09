import datetime as dt

from app.factcheck.dates import page_date, parse_date


def test_jsonld_date_modified_comes_first():
    html = """<html><head>
    <script type="application/ld+json">
    {"@type":"Article","datePublished":"2024-01-02","dateModified":"2025-03-04T10:00:00Z"}
    </script>
    <meta property="article:published_time" content="2023-01-01">
    </head><body><h1>Title</h1></body></html>"""
    assert page_date(html) == dt.date(2025, 3, 4)


def test_jsonld_graph_date_published():
    html = """<script type="application/ld+json">
    {"@graph": [{"@type": "WebSite"}, {"@type": "BlogPosting", "datePublished": "2026-09-18"}]}
    </script>"""
    assert page_date(html) == dt.date(2026, 9, 18)


def test_meta_modified_time():
    html = '<meta property="article:modified_time" content="2026-08-15T17:32:23+00:00">'
    assert page_date(html) == dt.date(2026, 8, 15)


def test_time_element():
    html = '<article><h1>Post</h1><time datetime="2022-03-31">March 31</time></article>'
    assert page_date(html) == dt.date(2022, 3, 31)


def test_visible_date_near_title():
    html = "<body><h1>Protecting against retry storms</h1><p>September 18, 2026 / Engineering</p></body>"
    assert page_date(html) == dt.date(2026, 9, 18)


def test_no_date_gives_none():
    assert page_date("<html><body><h1>Docs</h1><p>No date here.</p></body></html>") is None


def test_script_text_is_not_a_visible_date():
    html = "<body><script>var d='2020-01-01';</script><p>Hello</p></body>"
    assert page_date(html) is None


def test_parse_date_formats():
    assert parse_date("18 Sep 2026") == dt.date(2026, 9, 18)
    assert parse_date("Sept. 2, 2025") == dt.date(2025, 9, 2)
    assert parse_date("2026-13-40") is None
