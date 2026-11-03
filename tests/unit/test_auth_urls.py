"""Unit tests for auth redirect and callback URL helpers."""

import pytest


@pytest.mark.unit
def test_google_callback_uri_uses_request_host(app, test_db, monkeypatch):
    from app.routes.auth import _google_callback_uri

    monkeypatch.setitem(app.config, "GOOGLE_REDIRECT_URI", "")
    with app.test_request_context("/_api/auth/google/login", base_url="https://events-a.example"):
        assert _google_callback_uri() == "https://events-a.example/_api/auth/google/callback"


@pytest.mark.unit
@pytest.mark.parametrize(
    "callback",
    [
        "http://localhost:8080/_api/auth/google/callback",
        "https://events.example/arctos/_api/auth/google/callback",
    ],
)
def test_google_callback_uri_uses_configured_public_url(app, monkeypatch, callback):
    from app.routes.auth import _google_callback_uri

    monkeypatch.setitem(app.config, "GOOGLE_REDIRECT_URI", callback)
    # Dioxus replaces Host with the private backend address on both requests.
    with app.test_request_context("/_api/auth/google/login", base_url="http://127.0.0.1:5006"):
        assert _google_callback_uri() == callback
    with app.test_request_context("/_api/auth/google/callback", base_url="http://127.0.0.1:5006"):
        assert _google_callback_uri() == callback


@pytest.mark.unit
@pytest.mark.parametrize(
    "callback",
    [
        "/_api/auth/google/callback",
        "//events.example/_api/auth/google/callback",
        "javascript:alert(1)",
        "ftp://events.example/_api/auth/google/callback",
        "https:///missing-host",
        "https://user:secret@events.example/callback",
        "https://events.example/callback?next=/",
        "https://events.example/callback?",
        "https://events.example/callback#fragment",
        "https://events.example/callback#",
        "https://events.example:invalid/callback",
        "https://events.example:0/callback",
        "https://events.example\\other/callback",
        "https://events.example/\ncallback",
        "https://events.example/ callback",
    ],
)
def test_google_callback_uri_rejects_invalid_configuration(app, monkeypatch, callback):
    from app.routes.auth import _google_callback_uri

    monkeypatch.setitem(app.config, "GOOGLE_REDIRECT_URI", callback)
    with app.test_request_context("/_api/auth/google/login"):
        with pytest.raises(ValueError, match="GOOGLE_REDIRECT_URI must be"):
            _google_callback_uri()


@pytest.mark.unit
def test_google_callback_uri_loads_environment_setting(monkeypatch):
    from app import create_app

    callback = "http://localhost:8080/_api/auth/google/callback"
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", callback)
    configured_app = create_app(config={"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert configured_app.config["GOOGLE_REDIRECT_URI"] == callback


@pytest.mark.unit
def test_redirect_to_frontend_preserves_script_root(app, test_db):
    from app.routes.auth import _redirect_to_frontend

    with app.test_request_context(
        "/_api/auth/google/callback",
        base_url="https://events-b.example",
        environ_overrides={"SCRIPT_NAME": "/arctos"},
    ):
        response = _redirect_to_frontend("/auth/google/choose-account-type")
        assert response.location == "/arctos/auth/google/choose-account-type"
