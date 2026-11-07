"""OAuth 2.0 provider models ("Sign in with Arctos").

Defines :class:`OAuthClient` (a third-party application registered by an
Arctos user), :class:`OAuthAuthorizationCode` (a short-lived, single-use
code from the authorization endpoint), and :class:`OAuthToken` (an access
token plus its optional refresh token).

Secrets, codes, and tokens are stored as SHA-256 hashes; the plaintext
values only ever exist in the response that hands them to the client.
The classes implement the duck-typed interfaces Authlib's authorization
server expects (``ClientMixin``, ``AuthorizationCodeMixin``,
``TokenMixin``); see :mod:`app.utils.oauth_server`.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta
from urllib.parse import urlsplit

from app.models.base import db
from app.models.constants import (
    AUTH_STRING_LEN,
    LONG_URL_LEN,
    SHA256_HEX_LEN,
    SHORT_CODE_LEN,
    SHORT_NAME_LEN,
    USER_ID_LEN,
)
from app.utils.datetime_helpers import now_utc_naive

CLIENT_ID_LEN = 32
SCOPE_LEN = 255
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def secret_hash(value: str) -> str:
    """Return the stored form of a client secret, code, or token."""
    return hashlib.sha256(value.encode()).hexdigest()


def is_loopback_redirect_uri(uri: str) -> bool:
    """Whether *uri* is an ``http`` loopback redirect (RFC 8252 section 7.3)."""
    parts = urlsplit(uri)
    return parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS


def _ignoring_port(uri: str) -> tuple:
    parts = urlsplit(uri)
    return parts.hostname, parts.path, parts.query


class OAuthClient(db.Model):
    """A third-party application that users can sign in to with Arctos.

    Any signed-in user may register clients; there is no allowlist.
    Clients without a secret are *public* (native and browser apps) and
    must use PKCE. Clients with a secret are *confidential* and may also
    use the client-credentials grant to read public data on their own
    behalf.

    Attributes:
        client_id: Public identifier sent in authorization requests.
        client_secret_hash: Hash of the client secret; ``None`` for public clients.
        name: Application name shown on the consent screen.
        homepage_url: Optional link shown on the consent screen.
        redirect_uris: Newline-separated registered redirect URIs. Loopback
            URIs match on any port, as RFC 8252 requires for native apps.
        owner_id / owner_type: The Player or Team that registered the client.
        created_at: Registration time.
    """

    __tablename__ = "oauth_clients"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(CLIENT_ID_LEN), unique=True, nullable=False, index=True)
    client_secret_hash = db.Column(db.String(SHA256_HEX_LEN), nullable=True)
    name = db.Column(db.String(SHORT_NAME_LEN), nullable=False)
    homepage_url = db.Column(db.String(LONG_URL_LEN), nullable=True)
    redirect_uris = db.Column(db.Text, nullable=False, default="")
    owner_id = db.Column(db.String(USER_ID_LEN), nullable=False, index=True)
    owner_type = db.Column(db.String(SHORT_CODE_LEN), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now_utc_naive)

    @property
    def is_public(self) -> bool:
        return self.client_secret_hash is None

    @property
    def redirect_uri_list(self) -> list[str]:
        return [uri for uri in self.redirect_uris.splitlines() if uri]

    def get_client_id(self) -> str:
        return self.client_id

    def get_default_redirect_uri(self) -> str | None:
        uris = self.redirect_uri_list
        return uris[0] if len(uris) == 1 else None

    def get_allowed_scope(self, scope: str) -> str:
        from app.utils.oauth_server import SCOPES, SILENT_SCOPES

        if not scope:
            return ""
        allowed = set(SCOPES) | SILENT_SCOPES
        return " ".join(part for part in scope.split() if part in allowed)

    def check_redirect_uri(self, redirect_uri: str) -> bool:
        if is_loopback_redirect_uri(redirect_uri):
            return _ignoring_port(redirect_uri) in {
                _ignoring_port(uri) for uri in self.redirect_uri_list if is_loopback_redirect_uri(uri)
            }
        return redirect_uri in self.redirect_uri_list

    def check_client_secret(self, client_secret: str) -> bool:
        if self.client_secret_hash is None:
            return False
        return hmac.compare_digest(self.client_secret_hash, secret_hash(client_secret))

    def check_endpoint_auth_method(self, method: str, endpoint: str) -> bool:
        if self.is_public:
            return method == "none"
        return method in ("client_secret_basic", "client_secret_post")

    def check_response_type(self, response_type: str) -> bool:
        return response_type == "code"

    def check_grant_type(self, grant_type: str) -> bool:
        if grant_type == "client_credentials":
            return not self.is_public
        return grant_type in ("authorization_code", "refresh_token")


class OAuthAuthorizationCode(db.Model):
    """A single-use authorization code awaiting exchange at the token endpoint."""

    __tablename__ = "oauth_authorization_codes"

    LIFETIME = timedelta(minutes=5)

    id = db.Column(db.Integer, primary_key=True)
    code_hash = db.Column(db.String(SHA256_HEX_LEN), unique=True, nullable=False)
    client_id = db.Column(
        db.String(CLIENT_ID_LEN),
        db.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(db.String(USER_ID_LEN), nullable=False)
    user_type = db.Column(db.String(SHORT_CODE_LEN), nullable=False)
    redirect_uri = db.Column(db.String(LONG_URL_LEN), nullable=False)
    scope = db.Column(db.String(SCOPE_LEN), nullable=False, default="")
    code_challenge = db.Column(db.String(AUTH_STRING_LEN), nullable=True)
    code_challenge_method = db.Column(db.String(SHORT_CODE_LEN), nullable=True)
    nonce = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now_utc_naive)

    def is_expired(self) -> bool:
        return now_utc_naive() > self.created_at + self.LIFETIME

    def get_redirect_uri(self) -> str:
        return self.redirect_uri

    def get_scope(self) -> str:
        return self.scope

    def get_nonce(self) -> str | None:
        return self.nonce

    def get_auth_time(self) -> int:
        return int(self.created_at.timestamp())

    def get_acr(self) -> None:
        return None

    def get_amr(self) -> None:
        return None


class OAuthToken(db.Model):
    """An access token, with a refresh token for user-authorized grants.

    Refresh tokens rotate: each use revokes this row's refresh token and
    issues a new row; the old access token stays valid until it expires,
    so a resource server holding it is not cut off mid-request.
    Presenting an already-rotated refresh token revokes every token of
    that user/client pair, since it means the refresh token leaked.
    Client-credentials tokens have no user and no refresh token.
    """

    __tablename__ = "oauth_tokens"

    REFRESH_LIFETIME = timedelta(days=90)

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(
        db.String(CLIENT_ID_LEN),
        db.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(db.String(USER_ID_LEN), nullable=True, index=True)
    user_type = db.Column(db.String(SHORT_CODE_LEN), nullable=True)
    scope = db.Column(db.String(SCOPE_LEN), nullable=False, default="")
    access_token_hash = db.Column(db.String(SHA256_HEX_LEN), unique=True, nullable=False)
    refresh_token_hash = db.Column(db.String(SHA256_HEX_LEN), unique=True, nullable=True)
    issued_at = db.Column(db.DateTime, nullable=False, default=now_utc_naive)
    expires_in = db.Column(db.Integer, nullable=False)
    access_token_revoked = db.Column(db.Boolean, nullable=False, default=False)
    refresh_token_revoked = db.Column(db.Boolean, nullable=False, default=False)
    last_used_at = db.Column(db.DateTime, nullable=True)

    client = db.relationship("OAuthClient", lazy="joined")

    def check_client(self, client: OAuthClient) -> bool:
        return self.client_id == client.client_id

    def get_scope(self) -> str:
        return self.scope

    def get_expires_in(self) -> int:
        return self.expires_in

    def is_expired(self) -> bool:
        return now_utc_naive() > self.issued_at + timedelta(seconds=self.expires_in)

    def is_refresh_token_usable(self) -> bool:
        return (
            self.refresh_token_hash is not None
            and not self.refresh_token_revoked
            and now_utc_naive() <= self.issued_at + self.REFRESH_LIFETIME
        )

    def is_revoked(self) -> bool:
        return self.access_token_revoked

    def get_user(self):
        from app.services._common import resolve_actor

        if self.user_id is None:
            return None
        return resolve_actor(self.user_id, self.user_type)

    def get_client(self) -> OAuthClient:
        return self.client


class OidcSigningKey(db.Model):
    """The RSA key Arctos uses to sign OpenID Connect ID tokens.

    One row, shared by every worker. The private key stays in the database;
    only the public half is published at the JWKS endpoint.
    """

    __tablename__ = "oidc_signing_keys"

    id = db.Column(db.Integer, primary_key=True)
    kid = db.Column(db.String(AUTH_STRING_LEN), nullable=False)
    private_pem = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now_utc_naive)
