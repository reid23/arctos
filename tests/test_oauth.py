"""Integration tests for the OAuth 2.0 provider ("Sign in with Arctos")."""

import base64
import hashlib
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from flask import g

from tests.utils import login_as

pytestmark = pytest.mark.integration

LOOPBACK_REDIRECT = "http://127.0.0.1/callback"
CODE_VERIFIER = "a" * 64


@pytest.fixture(autouse=True)
def insecure_transport(monkeypatch):
    monkeypatch.setenv("AUTHLIB_INSECURE_TRANSPORT", "1")


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _register(client, **overrides):
    body = {
        "name": "Recorder",
        "redirect_uris": [LOOPBACK_REDIRECT],
        "confidential": False,
    } | overrides
    response = client.post("/_api/oauth/clients", json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _authorization_query(client_id, redirect_uri="http://127.0.0.1:53123/callback", **overrides):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": "xyz",
        "code_challenge": _challenge(CODE_VERIFIER),
        "code_challenge_method": "S256",
    } | overrides
    return urlencode({key: value for key, value in params.items() if value is not None})


def _authorize(client, client_id, **overrides):
    query = _authorization_query(client_id, **overrides)
    response = client.post(f"/_api/oauth/authorize?{query}", json={"approve": True})
    assert response.status_code == 200, response.get_json()
    return parse_qs(urlsplit(response.get_json()["redirect"]).query)


def _exchange(client, client_id, code, redirect_uri="http://127.0.0.1:53123/callback", **overrides):
    form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": CODE_VERIFIER,
    } | overrides
    return client.post("/api/oauth/token", data={key: value for key, value in form.items() if value is not None})


def _sign_in(client, player, scope=None):
    login_as(client, player)
    client_id = _register(client)["client"]["client_id"]
    code = _authorize(client, client_id, scope=scope)["code"][0]
    response = _exchange(client, client_id, code)
    assert response.status_code == 200, response.get_json()
    return client_id, response.get_json()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_rejects_plain_http_redirect(client, player):
    login_as(client, player)
    response = client.post(
        "/_api/oauth/clients",
        json={"name": "Bad", "redirect_uris": ["http://example.com/cb"], "confidential": False},
    )
    assert response.status_code == 400


def test_register_requires_login(client):
    response = client.post("/_api/oauth/clients", json={"name": "x", "redirect_uris": [LOOPBACK_REDIRECT]})
    assert response.status_code == 401


def test_consent_describes_request(client, player):
    login_as(client, player)
    client_id = _register(client)["client"]["client_id"]
    response = client.get(f"/_api/oauth/authorize?{_authorization_query(client_id, scope='injuries')}")
    body = response.get_json()
    assert response.status_code == 200
    assert body["client"]["name"] == "Recorder"
    assert [scope["name"] for scope in body["scopes"]] == ["injuries"]
    assert body["user"]["id"] == player.id


def test_unregistered_redirect_is_not_followed(client, player):
    login_as(client, player)
    client_id = _register(client)["client"]["client_id"]
    query = _authorization_query(client_id, redirect_uri="https://evil.example/cb")
    response = client.post(f"/_api/oauth/authorize?{query}", json={"approve": True})
    assert response.status_code == 400
    assert "redirect" not in response.get_json()


def test_deny_redirects_with_access_denied(client, player):
    login_as(client, player)
    client_id = _register(client)["client"]["client_id"]
    query = _authorization_query(client_id)
    response = client.post(f"/_api/oauth/authorize?{query}", json={"approve": False})
    params = parse_qs(urlsplit(response.get_json()["redirect"]).query)
    assert params["error"] == ["access_denied"]
    assert params["state"] == ["xyz"]


def test_pkce_flow_on_any_loopback_port(client, player):
    client_id, token = _sign_in(client, player)
    assert token["token_type"].lower() == "bearer"
    assert "refresh_token" in token
    userinfo = client.get("/api/oauth/userinfo", headers=_bearer(token["access_token"]))
    assert userinfo.get_json() == {
        "sub": player.id,
        "preferred_username": player.id,
        "id": player.id,
        "name": player.name,
        "type": "player",
    }


def test_public_client_needs_code_verifier(client, player):
    login_as(client, player)
    client_id = _register(client)["client"]["client_id"]
    code = _authorize(client, client_id)["code"][0]
    response = _exchange(client, client_id, code, code_verifier=None)
    assert response.status_code == 400


def test_code_is_single_use(client, player):
    login_as(client, player)
    client_id = _register(client)["client"]["client_id"]
    code = _authorize(client, client_id)["code"][0]
    assert _exchange(client, client_id, code).status_code == 200
    assert _exchange(client, client_id, code).status_code == 400


def test_bearer_is_denied_by_default(client, player):
    _, token = _sign_in(client, player)
    client.get("/_api/logout")
    assert client.get("/_api/me", headers=_bearer(token["access_token"])).status_code == 401


def test_session_does_not_satisfy_bearer_routes(client, player):
    login_as(client, player)
    assert client.get("/api/oauth/userinfo").status_code == 401


def test_refresh_rotates_and_detects_reuse(client, player):
    client_id, first = _sign_in(client, player)
    refresh = {"grant_type": "refresh_token", "client_id": client_id}
    second = client.post("/api/oauth/token", data=refresh | {"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    second = second.get_json()
    assert second["refresh_token"] != first["refresh_token"]
    assert client.get("/api/oauth/userinfo", headers=_bearer(first["access_token"])).status_code == 200

    reused = client.post("/api/oauth/token", data=refresh | {"refresh_token": first["refresh_token"]})
    assert reused.status_code == 400
    assert client.get("/api/oauth/userinfo", headers=_bearer(second["access_token"])).status_code == 401
    third = client.post("/api/oauth/token", data=refresh | {"refresh_token": second["refresh_token"]})
    assert third.status_code == 400


def test_revoke(client, player):
    client_id, token = _sign_in(client, player)
    response = client.post("/api/oauth/revoke", data={"client_id": client_id, "token": token["refresh_token"]})
    assert response.status_code == 200
    assert client.get("/api/oauth/userinfo", headers=_bearer(token["access_token"])).status_code == 401


def test_connected_apps_and_disconnect(client, player):
    client_id, token = _sign_in(client, player, scope="injuries")
    apps = client.get("/_api/oauth/connected-apps").get_json()["apps"]
    assert [(app["client_id"], [scope["name"] for scope in app["scopes"]]) for app in apps] == [
        (client_id, ["injuries"])
    ]
    assert client.delete(f"/_api/oauth/connected-apps/{client_id}").status_code == 200
    assert client.get("/_api/oauth/connected-apps").get_json()["apps"] == []
    assert client.get("/api/oauth/userinfo", headers=_bearer(token["access_token"])).status_code == 401


def test_client_credentials_for_confidential_clients(client, player):
    login_as(client, player)
    registered = _register(client, confidential=True, redirect_uris=["https://app.example/cb"])
    credentials = (registered["client"]["client_id"], registered["client_secret"])
    basic = base64.b64encode(":".join(credentials).encode()).decode()
    response = client.post(
        "/api/oauth/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
    )
    assert response.status_code == 200, response.get_json()
    token = response.get_json()
    assert "refresh_token" not in token
    assert client.get("/api/oauth/userinfo", headers=_bearer(token["access_token"])).status_code == 403

    scoped = client.post(
        "/api/oauth/token",
        data={"grant_type": "client_credentials", "scope": "injuries"},
        headers={"Authorization": f"Basic {basic}"},
    )
    assert scoped.status_code == 400


def test_public_clients_cannot_use_client_credentials(client, player):
    login_as(client, player)
    client_id = _register(client)["client"]["client_id"]
    response = client.post("/api/oauth/token", data={"grant_type": "client_credentials", "client_id": client_id})
    assert response.status_code in (400, 401)


def test_rotate_secret_and_delete(client, player):
    login_as(client, player)
    registered = _register(client, confidential=True, redirect_uris=["https://app.example/cb"])
    client_id = registered["client"]["client_id"]
    rotated = client.post(f"/_api/oauth/clients/{client_id}/secret").get_json()
    assert rotated["client_secret"] != registered["client_secret"]
    assert client.delete(f"/_api/oauth/clients/{client_id}").status_code == 200
    assert client.get("/_api/oauth/clients").get_json()["clients"] == []


def test_openid_connect_code_flow(client, player):
    from authlib.jose import JsonWebKey, jwt

    player.email = "player@example.com"
    from models import db

    db.session.commit()
    login_as(client, player)
    client_id = _register(client)["client"]["client_id"]
    discovery = client.get("/.well-known/openid-configuration")
    assert discovery.status_code == 200
    metadata = discovery.get_json()
    assert metadata["authorization_endpoint"].endswith("/oauth/authorize")
    assert metadata["jwks_uri"].endswith("/api/oauth/jwks")
    assert "RS256" in metadata["id_token_signing_alg_values_supported"]
    assert metadata["issuer"] == metadata["jwks_uri"].removesuffix("/api/oauth/jwks")
    assert "email" in metadata["scopes_supported"]
    assert "email" in metadata["claims_supported"]
    assert "email_verified" not in metadata["claims_supported"]

    rejected = client.get(f"/_api/oauth/authorize?{_authorization_query(client_id, scope='openid address')}")
    assert parse_qs(urlsplit(rejected.get_json()["redirect"]).query)["error"] == ["invalid_scope"]

    first = client.get(f"/_api/oauth/authorize?{_authorization_query(client_id, scope='openid profile email')}")
    assert first.get_json()["already_allowed"] is False
    assert "email" not in {scope["name"] for scope in first.get_json()["scopes"]}
    assert "email" not in first.get_data(as_text=True)
    code = _authorize(client, client_id, scope="openid profile email", nonce="nonce-1")["code"][0]
    token = _exchange(client, client_id, code).get_json()
    assert set(token["scope"].split()) == {"openid", "profile", "email"}
    keys = JsonWebKey.import_key_set(client.get("/api/oauth/jwks").get_json())
    claims = jwt.decode(token["id_token"], keys)
    claims.validate()
    assert claims["iss"] == metadata["issuer"]
    assert claims["sub"] == player.id
    assert claims["email"] == player.id
    assert claims["email"] != player.email
    assert claims["nonce"] == "nonce-1"
    assert client_id in claims["aud"] or claims["aud"] == client_id
    assert "email_verified" not in claims
    assert claims["name"] == player.name

    userinfo = client.get("/api/oauth/userinfo", headers=_bearer(token["access_token"])).get_json()
    assert userinfo["sub"] == player.id
    assert userinfo["email"] == player.id
    assert userinfo["email"] != player.email
    assert "email_verified" not in userinfo
    assert userinfo["id"] == player.id
    apps = client.get("/_api/oauth/connected-apps").get_json()["apps"]
    assert "email" not in {scope["name"] for app in apps for scope in app["scopes"]}

    refreshed = client.post(
        "/api/oauth/token",
        data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": token["refresh_token"]},
    )
    assert refreshed.status_code == 200, refreshed.get_json()
    refreshed_claims = jwt.decode(refreshed.get_json()["id_token"], keys)
    refreshed_claims.validate()
    assert refreshed_claims["sub"] == player.id
    assert refreshed_claims["email"] == player.id
    assert "nonce" not in refreshed_claims

    again = client.get(f"/_api/oauth/authorize?{_authorization_query(client_id, scope='openid profile email')}")
    assert again.get_json()["already_allowed"] is True
    assert "email" not in again.get_data(as_text=True)
    prior = _register(client)["client"]["client_id"]
    prior_code = _authorize(client, prior, scope="openid")["code"][0]
    assert _exchange(client, prior, prior_code).status_code == 200
    silent_email = client.get(
        f"/_api/oauth/authorize?{_authorization_query(prior, scope='openid email', prompt='none')}"
    )
    silent_email_params = parse_qs(urlsplit(silent_email.get_json()["redirect"]).query)
    assert "code" in silent_email_params
    silent_token = _exchange(client, prior, silent_email_params["code"][0]).get_json()
    assert set(silent_token["scope"].split()) == {"openid", "email"}
    silent_claims = jwt.decode(silent_token["id_token"], keys)
    silent_claims.validate()
    assert silent_claims["email"] == player.id
    wider = client.get(f"/_api/oauth/authorize?{_authorization_query(client_id, scope='openid injuries')}")
    assert wider.get_json()["already_allowed"] is False

    silent = client.get(f"/_api/oauth/authorize?{_authorization_query(client_id, scope='openid', prompt='none')}")
    silent_params = parse_qs(urlsplit(silent.get_json()["redirect"]).query)
    assert "code" in silent_params
    denied = client.get(
        f"/_api/oauth/authorize?{_authorization_query(client_id, scope='openid injuries', prompt='none')}"
    )
    assert parse_qs(urlsplit(denied.get_json()["redirect"]).query)["error"] == ["consent_required"]

    client.get("/_api/logout")
    logged_out = client.get(f"/_api/oauth/authorize?{_authorization_query(client_id, scope='openid', prompt='none')}")
    assert parse_qs(urlsplit(logged_out.get_json()["redirect"]).query)["error"] == ["login_required"]


def test_apps_are_private_to_their_owner(client, player, team):
    login_as(client, player)
    client_id = _register(client)["client"]["client_id"]
    login_as(client, team)
    # test_db holds one app context for the whole test, so Flask-Login's per-request user cache would persist.
    g.pop("_login_user", None)
    assert client.delete(f"/_api/oauth/clients/{client_id}").status_code == 404
    assert client.get("/_api/oauth/clients").get_json()["clients"] == []
