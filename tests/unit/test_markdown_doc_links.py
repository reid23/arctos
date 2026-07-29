"""Tests for markdown doc page link rewriting."""

import re

import pytest

from app.routes.content import MARKDOWN_PAGE_MAPPING, rewrite_doc_md_links


@pytest.mark.unit
def test_rewrite_doc_md_links_basic():
    html = '<a href="privacy-policy.md">Privacy</a>'
    assert rewrite_doc_md_links(html) == '<a href="/privacy-policy">Privacy</a>'


@pytest.mark.unit
def test_rewrite_doc_md_links_with_fragment():
    html = '<a href="docs.md#penalties">Penalties</a>'
    assert rewrite_doc_md_links(html) == '<a href="/docs#penalties">Penalties</a>'


@pytest.mark.unit
def test_rewrite_doc_md_links_leaves_external_md_urls():
    html = '<a href="https://github.com/reid23/arctos/CONTRIBUTING.md">Contributing</a>'
    assert rewrite_doc_md_links(html) == html


@pytest.mark.unit
def test_rewrite_doc_md_links_leaves_unknown_md_files():
    html = '<a href="not-a-served-doc.md">Nope</a>'
    assert rewrite_doc_md_links(html) == html


@pytest.mark.unit
def test_rewrite_doc_md_links_all_known_slugs():
    for slug in MARKDOWN_PAGE_MAPPING:
        html = f'<p><a href="{slug}.md">x</a> <a href="{slug}.md#sec">y</a></p>'
        out = rewrite_doc_md_links(html)
        assert f'href="/{slug}"' in out
        assert f'href="/{slug}#sec"' in out
        assert f'href="{slug}.md' not in out


@pytest.mark.unit
def test_markdown_page_docs_rewrites_internal_md_links(client):
    resp = client.get("/_api/markdown/docs")
    assert resp.status_code == 200
    data = resp.get_json()
    html = data["html"]

    # Internal doc links become app routes.
    assert 'href="/privacy-policy"' in html
    assert 'href="/data-accessibility-guide"' in html
    assert 'href="/arctos-schedule-script"' in html

    # No leftover relative .md hrefs to served docs.
    leftover = re.findall(
        r'href="(?:' + "|".join(re.escape(s) for s in MARKDOWN_PAGE_MAPPING) + r')\.md(?:#[^"]*)?"',
        html,
    )
    assert leftover == []

    # External .md links (GitHub) stay intact.
    assert "https://github.com/reid23/arctos/CONTRIBUTING.md" in html


@pytest.mark.unit
def test_markdown_page_cross_doc_fragment(client):
    resp = client.get("/_api/markdown/data-accessibility-guide")
    assert resp.status_code == 200
    html = resp.get_json()["html"]
    assert 'href="/docs#penalties"' in html
    assert "docs.md#penalties" not in html
