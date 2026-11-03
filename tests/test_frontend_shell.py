"""Document/asset delivery contracts independent of a local Rust installation."""

import gzip
import json
import os
import stat

import pytest
from flask import Flask

from app.services.frontend_shell import init_frontend_shell, render_application_shell
from scripts.prepare_public_assets import prepare_dist, prepare_presentation

pytestmark = pytest.mark.integration


@pytest.fixture
def frontend_dist(app, tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-dxh123abc.js").write_text('fetch("/assets/app-dxh123abc.wasm");')
    (assets / "app-dxh123abc.wasm").write_bytes(b"\x00asm" + b"fixture" * 200)
    (tmp_path / "index.html").write_text('<script type="module" src="/./assets/app-dxh123abc.js"></script>')
    prepare_dist(tmp_path)
    monkeypatch.setitem(app.config, "ARCTOS_FRONTEND_DIST", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("name", ["bootstrap.css", "design.css", "theme.css"])
def test_prepared_presentation_is_served_compressed_and_version_cached(tmp_path, name):
    from app.services.frontend_shell import public_asset

    directory = tmp_path / "public"
    prepare_presentation(directory)
    generated = (directory / name).read_bytes()
    prepare_presentation(directory)
    assert (directory / name).read_bytes() == generated

    app = Flask(__name__, static_folder=tmp_path, static_url_path="/static")
    init_frontend_shell(app)
    with app.test_request_context("/"):
        path = public_asset(name)
    client = app.test_client()
    plain = client.get(path, headers={"Accept-Encoding": "identity"})
    compressed = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert plain.status_code == compressed.status_code == 200
    assert plain.data == generated
    assert plain.mimetype == compressed.mimetype == "text/css"
    assert gzip.decompress(compressed.data) == plain.data
    assert len(compressed.data) < len(plain.data)
    assert "immutable" in compressed.headers["Cache-Control"]
    assert "Accept-Encoding" in compressed.vary
    assert (
        client.get(path, headers={"Accept-Encoding": "gzip", "If-None-Match": compressed.headers["ETag"]}).status_code
        == 304
    )
    assert client.get(path, headers={"Accept-Encoding": "gzip;q=0"}).data == plain.data
    assert "immutable" not in client.get(f"/static/public/{name}?v=old").headers["Cache-Control"]
    assert client.get("/static/public/missing.css").status_code == 404


def test_session_static_response_cannot_be_shared(app):
    from app.services.frontend_shell import public_asset

    with app.test_request_context("/"):
        path = public_asset("design.css")
    client = app.test_client()
    client.set_cookie(app.config["SESSION_COOKIE_NAME"], "invalid-session")
    response = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.cache_control.private
    assert not response.cache_control.public


def test_shell_preserves_context_and_accessible_feedback_until_application_starts(app, frontend_dist, monkeypatch):
    monkeypatch.setitem(app.config, "PUBLIC_HTML_ENABLED", True)
    with app.test_request_context("/cup/register"):
        response = render_application_shell(
            {
                "event_name": "<script>bad</script>",
                "workflow_label": "registration",
                "return_url": "/cup",
                "return_label": "Back to event",
            }
        )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-public-html="true"' in html
    assert "&lt;script&gt;bad&lt;/script&gt;" in html
    assert html.index("Loading…") < html.index('id="main"')
    assert 'id="app-startup" class="arctos-app public-shell"' in html
    assert 'id="startup-content"' in html
    assert 'id="main" aria-hidden="true" inert' in html
    assert html.count('class="app-header"') == 1
    assert "bootstrap.css" in html and "design.css" in html
    assert 'data-public-return href="/cup"' in html
    assert "<noscript>" in html
    assert 'data-entry="/assets/app-dxh123abc.js"' in html
    assert '<link rel="modulepreload" href="/assets/app-dxh123abc.js">' in html
    assert html.index('rel="modulepreload"') < html.index("appearance.js")
    assert html.index('id="main"') < html.index("boot.js")
    assert "async></script>" in html  # CSS must not postpone the application request.
    assert 'type="module"' not in html  # boot.js imports exactly one generated entry.
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Robots-Tag"] == "noindex"


@pytest.mark.parametrize("path", ["/scoreboard-render", "/cup/scoreboard", "/cup/sidecomps/1/enter-results"])
def test_special_outputs_keep_their_own_document_chrome(app, frontend_dist, path):
    with app.test_request_context(path):
        response = render_application_shell()
    html = response.get_data(as_text=True)
    assert 'id="app-boot"' not in html
    assert "public.css" not in html
    assert 'data-public-background="true"' not in html
    assert 'id="app-startup"' not in html
    assert 'aria-hidden="true" inert' not in html
    assert '<script type="module" src="/assets/app-dxh123abc.js">' in html


@pytest.mark.parametrize(
    "bad_manifest",
    [
        None,
        {},
        {"version": 1, "entry": "/assets/../../private.js"},
        {"version": 1, "entry": "https://example.com/evil.js"},
        {"version": 1, "entry": "/assets/missing.js"},
    ],
)
def test_missing_or_invalid_build_is_readable_503(app, tmp_path, monkeypatch, bad_manifest):
    monkeypatch.setitem(app.config, "ARCTOS_FRONTEND_DIST", str(tmp_path))
    if bad_manifest is not None:
        (tmp_path / "arctos-assets.json").write_text(json.dumps(bad_manifest))
    with app.test_request_context("/login"):
        response = render_application_shell()
    assert response.status_code == 503
    assert "temporarily unavailable" in response.get_data(as_text=True)
    assert response.headers["Retry-After"] == "30"


def test_asset_identity_gzip_mime_and_validators(app, frontend_dist):
    client = app.test_client()
    path = "/assets/app-dxh123abc.wasm"
    plain = client.get(path, headers={"Accept-Encoding": "identity"})
    compressed = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert plain.mimetype == compressed.mimetype == "application/wasm"
    assert gzip.decompress(compressed.data) == plain.data
    assert "Content-Encoding" not in plain.headers
    assert compressed.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in compressed.vary
    assert "immutable" in plain.headers["Cache-Control"]
    assert client.get(path, headers={"If-None-Match": plain.headers["ETag"]}).status_code == 304
    assert client.get(path, headers={"Accept-Encoding": "gzip;q=0"}).data == plain.data


@pytest.mark.parametrize("path", ["missing.wasm", "../arctos-assets.json", "app-dxh123abc.wasm.gz"])
def test_missing_and_unsafe_assets_are_not_app_documents(app, frontend_dist, path):
    response = app.test_client().get("/assets/" + path)
    assert response.status_code == 404
    assert b'id="main"' not in response.data


def test_manifest_build_rejects_missing_wasm_and_external_entry(tmp_path):
    (tmp_path / "index.html").write_text('<script type="module" src="https://example.com/app.js"></script>')
    with pytest.raises(ValueError, match="local assets"):
        prepare_dist(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text('<script type="module" src="/assets/app.js"></script>')
    (assets / "app.js").write_text('fetch("/assets/missing.wasm")')
    with pytest.raises(ValueError, match="available Wasm"):
        prepare_dist(tmp_path)


def test_compressed_symlink_cannot_serve_outside_asset_root(app, frontend_dist, tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_bytes(b"not an asset")
    compressed = frontend_dist / "assets/app-dxh123abc.wasm.gz"
    compressed.unlink()
    compressed.symlink_to(secret)
    response = app.test_client().get("/assets/app-dxh123abc.wasm", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert "Content-Encoding" not in response.headers
    assert b"not an asset" not in response.data


def test_build_refuses_wasm_escape_and_does_not_follow_compression_symlink(frontend_dist, tmp_path):
    entry = frontend_dist / "assets/app-dxh123abc.js"
    entry.write_text('fetch("/assets/../../outside.wasm")')
    with pytest.raises(ValueError, match="asset prefix"):
        prepare_dist(frontend_dist)
    entry.write_text('fetch("/assets/app-dxh123abc.wasm")')
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched")
    target = frontend_dist / "assets/app-dxh123abc.js.gz"
    target.unlink()
    target.symlink_to(outside)
    # Either the preflight symlink check rejects it or atomic replacement removes
    # the link; neither path can write through it.
    try:
        prepare_dist(frontend_dist)
    except ValueError:
        pass
    assert outside.read_text() == "untouched"


def test_subpath_requires_matching_compiled_asset_base(app, frontend_dist):
    (frontend_dist / "index.html").write_text('<script type="module" src="/jugger/assets/app-dxh123abc.js"></script>')
    (frontend_dist / "assets/app-dxh123abc.js").write_text('fetch("/jugger/assets/app-dxh123abc.wasm")')
    manifest = prepare_dist(frontend_dist)
    assert manifest["base_path"] == "/jugger"
    with app.test_request_context("/login?next=%2Fjugger%2Fcup", environ_overrides={"SCRIPT_NAME": "/jugger"}):
        response = render_application_shell()
    assert response.status_code == 200
    assert 'data-entry="/jugger/assets/app-dxh123abc.js"' in response.get_data(as_text=True)
    assert 'data-boot-retry href="/jugger/login?next=%2Fjugger%2Fcup"' in response.get_data(as_text=True)
    with app.test_request_context("/login"):
        assert render_application_shell().status_code == 503


def test_staging_retains_old_assets_and_previous_manifest(frontend_dist, tmp_path):
    from scripts.stage_frontend import stage

    # frontend_dist is tmp_path itself; deployment must be a sibling.
    target = tmp_path.with_name(tmp_path.name + "-deployment")
    stage(frontend_dist, target)
    old_manifest = (target / "arctos-assets.json").read_bytes()
    (target / "assets/previous-release.js").write_text("old open tab")
    stage(frontend_dist, target)
    assert (target / "assets/previous-release.js").read_text() == "old open tab"
    assert (target / "arctos-assets.json.previous").read_bytes() == old_manifest
    assert (target / "arctos-assets.json").read_bytes() == old_manifest


def test_staging_public_asset_permissions_survive_restrictive_umask(frontend_dist, tmp_path):
    from scripts.stage_frontend import stage

    target = tmp_path.with_name(tmp_path.name + "-deployment")
    previous_umask = os.umask(0o077)
    try:
        nested = frontend_dist / "assets" / "vendor" / "icons"
        nested.mkdir(parents=True)
        (nested / "font.woff2").write_bytes(b"public font fixture")
        stage(frontend_dist, target)
        for directory in (target, target / "assets", target / "assets/vendor", target / "assets/vendor/icons"):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o755
        copied_font = target / "assets/vendor/icons/font.woff2"
        assert copied_font.read_bytes() == b"public font fixture"
        assert stat.S_IMODE(copied_font.stat().st_mode) == 0o644
        assert stat.S_IMODE((target / "arctos-assets.json").stat().st_mode) == 0o644

        # Reusing unchanged content must also repair restrictive permissions.
        copied_font.chmod(0o600)
        copied_font.parent.chmod(0o700)
        stage(frontend_dist, target)
        assert stat.S_IMODE(copied_font.stat().st_mode) == 0o644
        assert stat.S_IMODE(copied_font.parent.stat().st_mode) == 0o755
    finally:
        os.umask(previous_umask)


def test_staging_rejects_modified_entry_before_touching_destination(frontend_dist, tmp_path):
    from scripts.stage_frontend import stage

    target = tmp_path.with_name(tmp_path.name + "-deployment")
    (frontend_dist / "assets/app-dxh123abc.js").write_text("changed after preparation")
    with pytest.raises(ValueError):
        stage(frontend_dist, target)
    assert not target.exists()


def test_staging_rejects_missing_wasm_and_keeps_the_working_release(frontend_dist, tmp_path):
    from scripts.stage_frontend import stage

    target = tmp_path.with_name(tmp_path.name + "-deployment")
    stage(frontend_dist, target)
    published_manifest = (target / "arctos-assets.json").read_bytes()
    published_index = (target / "index.html").read_bytes()
    (frontend_dist / "assets/app-dxh123abc.wasm").unlink()

    with pytest.raises(ValueError, match="available Wasm"):
        stage(frontend_dist, target)

    assert (target / "arctos-assets.json").read_bytes() == published_manifest
    assert (target / "index.html").read_bytes() == published_index
    assert (target / "assets/app-dxh123abc.wasm").is_file()
