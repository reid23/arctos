"""Sanitize user Markdown without losing the document features Arctos uses."""

from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.filters import render_markdown

pytestmark = pytest.mark.unit


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(str(html))

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@pytest.mark.parametrize(
    "source",
    [
        "<script>window.marker = true</script>",
        '<img src="x" onerror="window.marker = true">',
        '<a href="javascript:window.marker=true">click</a>',
        '<a href="jav&#x61;script:window.marker=true">click</a>',
        '<a href="java&#10;script:window.marker=true">click</a>',
        "[click](javascript:window.marker=true)",
        '<a href="data:text/html,marker">click</a>',
        '<a href="vbscript:marker">click</a>',
        '<img src="data:image/svg+xml,marker">',
        '<iframe srcdoc="<script>marker</script>"></iframe>',
        '<svg onload="marker"><foreignObject><p onmouseover="marker">text</p></foreignObject></svg>',
        "<math><mtext><img src=x onerror=marker></mtext></math>",
        '<object data="https://example.test/marker"></object><embed src="marker">',
        '<form action="https://example.test"><input autofocus onfocus="marker"><button>Submit</button></form>',
        '<base href="https://example.test"><meta http-equiv="refresh" content="0;url=https://example.test">',
        '<style>body { display: none }</style><p style="position:fixed;inset:0" onclick="marker">text</p>',
        '<!-- hidden --><a href="/safe" onpointerenter="marker">link</a>',
    ],
)
def test_markdown_removes_active_markup_and_unsafe_urls(source):
    html = render_markdown(source)
    nodes = Elements(html).nodes
    forbidden = {
        "script",
        "iframe",
        "svg",
        "math",
        "foreignobject",
        "object",
        "embed",
        "form",
        "input",
        "button",
        "base",
        "meta",
        "style",
    }
    assert not any(tag in forbidden for tag, _ in nodes), html
    for _, attrs in nodes:
        assert not any(name.startswith("on") or name in ("style", "srcdoc", "formaction") for name in attrs), html
        for name in ("href", "src"):
            if name in attrs:
                url = "".join(attrs[name].split()).lower()
                assert not url.startswith(("javascript:", "vbscript:", "data:")), html
    assert "<!--" not in str(html)


def test_markdown_preserves_document_formatting():
    html = str(
        render_markdown("""# Heading {#section}

**Bold** and *emphasis*, `inline code`.

> A quotation.

- First
- Second

1. One
2. Two

Term
: Definition

| Name | Value |
| ---- | ----- |
| Row | 42 |

```python
print("example.test")
```

!!! warning "Notice"
    Keep the formatting.

![Alternative text](/static/example.png "Image title")

A footnote[^1].

[^1]: Footnote text.

<details><summary>More</summary><p>Details text.</p></details>
""")
    )
    nodes = Elements(html).nodes
    tags = {tag for tag, _ in nodes}
    assert {
        "h1",
        "strong",
        "em",
        "code",
        "blockquote",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "table",
        "thead",
        "tbody",
        "th",
        "td",
        "pre",
        "img",
        "sup",
        "details",
        "summary",
    } <= tags
    assert ("h1", {"id": "section"}) in nodes
    assert any(attrs.get("class") == "language-python" for _, attrs in nodes)
    assert any(attrs.get("class") == "admonition warning" for _, attrs in nodes)
    assert any(attrs.get("class") == "admonition-title" for _, attrs in nodes)
    assert any(attrs.get("id") == "fn:1" for _, attrs in nodes)
    assert any(attrs.get("href") == "#fn:1" for _, attrs in nodes)
    assert any(
        attrs.get("src") == "/static/example.png" and attrs.get("alt") == "Alternative text" for _, attrs in nodes
    )
    assert '<a href="http://example.test"' not in html, "Code must remain literal"


@pytest.mark.parametrize(
    "href",
    [
        "https://example.test/path?q=one",
        "http://example.test",
        "mailto:team@example.test",
        "/demo-open",
        "../other",
        "#section",
    ],
)
def test_markdown_preserves_safe_links_and_protects_new_tabs(href):
    nodes = Elements(render_markdown(f'<a href="{href}" target="_blank" rel="opener">Link</a>')).nodes
    link = next(attrs for tag, attrs in nodes if tag == "a")
    assert link["href"] == href
    assert link["target"] == "_blank"
    assert set(link["rel"].split()) == {"nofollow", "noopener", "noreferrer"}


def test_markdown_rejects_application_classes_but_keeps_safe_anchors():
    nodes = Elements(
        render_markdown('<div class="modal show d-block" id="section"><span class="admonition-title">Text</span></div>')
    ).nodes
    assert any(attrs.get("id") == "section" for _, attrs in nodes)
    assert not any("modal" in attrs.get("class", "") for _, attrs in nodes)
    assert any(attrs.get("class") == "admonition-title" for _, attrs in nodes)


def test_markdown_linkifies_plain_urls():
    nodes = Elements(render_markdown("Visit https://example.org for details.")).nodes
    assert any(tag == "a" and attrs.get("href") == "https://example.org" for tag, attrs in nodes)


@pytest.mark.parametrize("tag,attribute", [("img", "width"), ("img", "height"), ("td", "colspan"), ("th", "rowspan")])
def test_oversized_numeric_attributes_are_removed_without_integer_conversion_errors(tag, attribute):
    markup = f'<{tag} {attribute}="{"9" * 5000}">content</{tag}>'
    if tag in ("td", "th"):
        markup = f"<table><tr>{markup}</tr></table>"
    nodes = Elements(render_markdown(markup)).nodes
    assert any(element == tag for element, _ in nodes)
    assert not any(attribute in attrs for _, attrs in nodes)


@pytest.mark.parametrize("source", [None, ""])
def test_empty_markdown_stays_empty(source):
    assert render_markdown(source) == ""


def test_render_markdown_api_returns_sanitized_html_with_existing_contract(app):
    with app.test_client() as client:
        response = client.post(
            "/_api/render-markdown", json={"markdown": '**Safe** <img src="/static/example.png" onerror="marker">'}
        )
    assert response.status_code == 200
    payload = response.get_json()
    assert set(payload) == {"html", "css"}
    assert "<strong>Safe</strong>" in payload["html"]
    assert "onerror" not in payload["html"]
    assert 'src="/static/example.png"' in payload["html"]


@pytest.mark.parametrize("filename", ["docs.md", "developers.md"])
def test_help_document_fragment_links_resolve_after_sanitization(filename):
    """TOC/task links must target IDs retained by the production renderer."""
    source = (Path(__file__).resolve().parents[2] / "docs" / filename).read_text()
    nodes = Elements(render_markdown(source)).nodes
    ids = {attrs["id"] for _, attrs in nodes if "id" in attrs}
    fragments = {attrs["href"][1:] for tag, attrs in nodes if tag == "a" and attrs.get("href", "").startswith("#")}
    assert fragments <= ids, f"{filename}: missing anchors {sorted(fragments - ids)}"
