"""OAuth client registration and connected-app management.

Any signed-in Player or Team can register OAuth clients ("apps") that
let people sign in with Arctos; there is no admin allowlist. This module
also lists and disconnects the apps a user has authorized.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.error_values import Err, Ok, Result, allow_Q
from app.exceptions import ArctosError
from app.models.base import db
from app.models.oauth import OAuthClient, OAuthToken, is_loopback_redirect_uri, secret_hash
from app.utils.oauth_server import SCOPES, SILENT_SCOPES, revoke_grant

MAX_REDIRECT_URIS = 10


@dataclass(frozen=True)
class ConnectedApp:
    client: OAuthClient
    scopes: list[str]
    last_used_at: object


def _validate_redirect_uri(uri: str) -> Result[str, ArctosError]:
    parts = urlsplit(uri)
    if parts.fragment:
        return Err(ArctosError(f"Redirect URI {uri!r} must not contain a fragment.", status_code=400, public=True))
    if parts.scheme == "https" and parts.netloc:
        return Ok(uri)
    if is_loopback_redirect_uri(uri):
        return Ok(uri)
    if parts.scheme not in ("http", "https", "") and "." in parts.scheme:
        return Ok(uri)
    return Err(
        ArctosError(
            f"Redirect URI {uri!r} must use https, an http loopback address (127.0.0.1, [::1], or localhost), "
            "or a reverse-domain private scheme such as com.example.app:/callback.",
            status_code=400,
            public=True,
        )
    )


@allow_Q
def _validated_redirect_uris(redirect_uris: list[str]) -> Result[str, ArctosError]:
    uris = [uri.strip() for uri in redirect_uris if uri.strip()]
    if not uris:
        return Err(ArctosError("Add at least one redirect URI.", status_code=400, public=True))
    if len(uris) > MAX_REDIRECT_URIS:
        return Err(
            ArctosError(f"An app can have at most {MAX_REDIRECT_URIS} redirect URIs.", status_code=400, public=True)
        )
    for uri in uris:
        _validate_redirect_uri(uri).Q()
    return Ok("\n".join(uris))


def _validated_name(name: str) -> Result[str, ArctosError]:
    name = (name or "").strip()
    if not name:
        return Err(ArctosError("App name is required.", status_code=400, public=True))
    return Ok(name)


def _validated_homepage(homepage_url: str | None) -> Result[str | None, ArctosError]:
    homepage_url = (homepage_url or "").strip() or None
    if homepage_url and urlsplit(homepage_url).scheme not in ("http", "https"):
        return Err(ArctosError("Homepage must be an http(s) URL.", status_code=400, public=True))
    return Ok(homepage_url)


@dataclass(frozen=True)
class OAuthService:
    @staticmethod
    @allow_Q
    def create_client(
        owner_id: str,
        owner_type: str,
        name: str,
        homepage_url: str | None,
        redirect_uris: list[str],
        confidential: bool,
    ) -> Result[tuple[OAuthClient, str | None], ArctosError]:
        """Register an app. Returns the client and, if confidential, its one-time-visible secret."""
        name = _validated_name(name).Q()
        homepage_url = _validated_homepage(homepage_url).Q()
        redirect_uris = _validated_redirect_uris(redirect_uris).Q()
        secret = secrets.token_urlsafe(32) if confidential else None
        client = OAuthClient(
            client_id=secrets.token_urlsafe(18),
            client_secret_hash=secret_hash(secret) if secret else None,
            name=name,
            homepage_url=homepage_url,
            redirect_uris=redirect_uris,
            owner_id=owner_id,
            owner_type=owner_type,
        )
        db.session.add(client)
        db.session.commit()
        return Ok((client, secret))

    @staticmethod
    def owned_client(owner_id: str, owner_type: str, client_id: str) -> Result[OAuthClient, ArctosError]:
        client = OAuthClient.query.filter_by(client_id=client_id, owner_id=owner_id, owner_type=owner_type).first()
        if client is None:
            return Err(ArctosError("App not found", status_code=404, public=True))
        return Ok(client)

    @staticmethod
    def list_clients(owner_id: str, owner_type: str) -> list[OAuthClient]:
        return (
            OAuthClient.query.filter_by(owner_id=owner_id, owner_type=owner_type)
            .order_by(OAuthClient.created_at.asc())
            .all()
        )

    @staticmethod
    @allow_Q
    def update_client(
        client: OAuthClient, name: str, homepage_url: str | None, redirect_uris: list[str]
    ) -> Result[OAuthClient, ArctosError]:
        name = _validated_name(name).Q()
        homepage_url = _validated_homepage(homepage_url).Q()
        redirect_uris = _validated_redirect_uris(redirect_uris).Q()
        client.name = name
        client.homepage_url = homepage_url
        client.redirect_uris = redirect_uris
        db.session.commit()
        return Ok(client)

    @staticmethod
    def rotate_secret(client: OAuthClient) -> Result[str, ArctosError]:
        if client.is_public:
            return Err(ArctosError("Public apps have no secret.", status_code=400, public=True))
        secret = secrets.token_urlsafe(32)
        client.client_secret_hash = secret_hash(secret)
        db.session.commit()
        return Ok(secret)

    @staticmethod
    def delete_client(client: OAuthClient) -> None:
        db.session.delete(client)
        db.session.commit()

    @staticmethod
    def grant_covers(user_id: str, user_type: str, client_id: str, requested: list[str]) -> bool:
        """Whether this user already allowed *client_id* every scope in *requested*.

        A disconnected app has no usable token, so the next sign-in asks again.
        """
        tokens = OAuthToken.query.filter_by(client_id=client_id, user_id=user_id, user_type=user_type).all()
        usable = [
            token
            for token in tokens
            if token.is_refresh_token_usable() or not (token.is_revoked() or token.is_expired())
        ]
        if not usable:
            return False
        granted = {scope for token in usable for scope in token.scope.split() if scope}
        visible = [scope for scope in requested if scope not in SILENT_SCOPES]
        return set(visible).issubset(granted)

    @staticmethod
    def connected_apps(user_id: str, user_type: str) -> list[ConnectedApp]:
        """Apps holding a token for this user that is still usable."""
        tokens = OAuthToken.query.filter_by(user_id=user_id, user_type=user_type).all()
        by_client: dict[str, list[OAuthToken]] = {}
        for token in tokens:
            if token.is_refresh_token_usable() or not (token.is_revoked() or token.is_expired()):
                by_client.setdefault(token.client_id, []).append(token)
        return [
            ConnectedApp(
                client=client_tokens[0].client,
                scopes=sorted({scope for token in client_tokens for scope in token.scope.split() if scope in SCOPES}),
                last_used_at=max(token.last_used_at or token.issued_at for token in client_tokens),
            )
            for client_tokens in by_client.values()
        ]

    @staticmethod
    def disconnect(user_id: str, user_type: str, client_id: str) -> None:
        revoke_grant(client_id, user_id, user_type)
