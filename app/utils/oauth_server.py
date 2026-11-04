"""OAuth 2.0 authorization server for "Sign in with Arctos".

Wires Authlib's :class:`~authlib.integrations.flask_oauth2.AuthorizationServer`
to the models in :mod:`app.models.oauth`:

* Authorization code grant with PKCE (``S256``), required for public
  clients (RFC 7636, RFC 8252).
* Refresh token grant with rotation and reuse detection.
* Client credentials grant for confidential clients reading public data.
* Token revocation (RFC 7009).

Bearer tokens are only honoured by routes decorated with
:func:`require_bearer`; Flask-Login never looks at the ``Authorization``
header, so every other route keeps rejecting them.
"""

from __future__ import annotations

import functools
import json

from authlib.integrations.flask_oauth2 import AuthorizationServer
from authlib.integrations.flask_oauth2.errors import raise_http_exception
from authlib.oauth2.rfc6749 import InvalidScopeError, grants
from authlib.oauth2.rfc6750 import BearerTokenValidator
from authlib.oauth2.rfc6750.errors import InsufficientScopeError, InvalidTokenError
from authlib.oauth2.rfc7009 import RevocationEndpoint
from authlib.oauth2.rfc7636 import CodeChallenge
from authlib.oidc.core.grants import OpenIDCode, OpenIDToken
from flask import g, request

from app.models.base import db
from app.models.oauth import OAuthAuthorizationCode, OAuthClient, OAuthToken, secret_hash
from app.services.permission_service import PermissionService
from app.utils.datetime_helpers import now_utc_naive

SCOPES = {
    "openid": "Confirm your identity",
    "profile": "See your profile photo",
    "injuries": "Read all of your injury records, including private ones",
    "footage": "See tournaments you organize, including unpublished ones, and upload match footage to them",
}
"""Scopes a client may request. Reading public data needs no scope."""

# Clients send this standard scope by habit. Arctos has no address to release,
# so the request succeeds and the scope is dropped.
IGNORED_SCOPES = frozenset({"email"})

ACCESS_TOKEN_LIFETIME_SECONDS = 3600
LAST_USED_RESOLUTION_SECONDS = 60
PUBLIC_AND_CONFIDENTIAL_AUTH_METHODS = ["client_secret_basic", "client_secret_post", "none"]

authorization_server = AuthorizationServer()


def _user_type(user) -> str:
    return str(PermissionService.user_type(user).unwrap())


def _query_client(client_id: str) -> OAuthClient | None:
    return OAuthClient.query.filter_by(client_id=client_id).first()


def _save_token(token: dict, oauth_request) -> None:
    user = oauth_request.user
    db.session.add(
        OAuthToken(
            client_id=oauth_request.client.client_id,
            user_id=user.id if user else None,
            user_type=_user_type(user) if user else None,
            scope=token.get("scope", ""),
            access_token_hash=secret_hash(token["access_token"]),
            refresh_token_hash=secret_hash(token["refresh_token"]) if "refresh_token" in token else None,
            expires_in=token["expires_in"],
        )
    )
    db.session.commit()


def revoke_grant(client_id: str, user_id: str, user_type: str) -> None:
    """Revoke every token *client_id* holds for the given user."""
    OAuthToken.query.filter_by(client_id=client_id, user_id=user_id, user_type=user_type).update(
        {"access_token_revoked": True, "refresh_token_revoked": True}
    )
    db.session.commit()


class S256CodeChallenge(CodeChallenge):
    DEFAULT_CODE_CHALLENGE_METHOD = "S256"
    SUPPORTED_CODE_CHALLENGE_METHOD = ["S256"]


class AuthorizationCodeGrant(grants.AuthorizationCodeGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = PUBLIC_AND_CONFIDENTIAL_AUTH_METHODS

    def save_authorization_code(self, code, oauth_request):
        db.session.add(
            OAuthAuthorizationCode(
                code_hash=secret_hash(code),
                client_id=oauth_request.client.client_id,
                user_id=oauth_request.user.id,
                user_type=_user_type(oauth_request.user),
                redirect_uri=oauth_request.payload.redirect_uri or "",
                scope=oauth_request.client.get_allowed_scope(oauth_request.payload.scope or ""),
                code_challenge=oauth_request.payload.data.get("code_challenge"),
                code_challenge_method=oauth_request.payload.data.get("code_challenge_method"),
                nonce=oauth_request.payload.data.get("nonce"),
            )
        )
        db.session.commit()

    def query_authorization_code(self, code, client):
        authorization_code = OAuthAuthorizationCode.query.filter_by(
            code_hash=secret_hash(code), client_id=client.client_id
        ).first()
        if authorization_code and not authorization_code.is_expired():
            return authorization_code
        return None

    def delete_authorization_code(self, authorization_code):
        db.session.delete(authorization_code)
        db.session.commit()

    def authenticate_user(self, authorization_code):
        from app.services._common import resolve_actor

        return resolve_actor(authorization_code.user_id, authorization_code.user_type)


class RefreshTokenGrant(grants.RefreshTokenGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = PUBLIC_AND_CONFIDENTIAL_AUTH_METHODS
    INCLUDE_NEW_REFRESH_TOKEN = True

    def authenticate_refresh_token(self, refresh_token):
        token = OAuthToken.query.filter_by(refresh_token_hash=secret_hash(refresh_token)).first()
        if token is None:
            return None
        if token.refresh_token_revoked:
            revoke_grant(token.client_id, token.user_id, token.user_type)
            return None
        return token if token.is_refresh_token_usable() else None

    def authenticate_user(self, refresh_token):
        return refresh_token.get_user()

    def revoke_old_credential(self, refresh_token):
        refresh_token.refresh_token_revoked = True
        db.session.commit()


class ClientCredentialsGrant(grants.ClientCredentialsGrant):
    """Lets a confidential client read public data as itself."""

    TOKEN_ENDPOINT_AUTH_METHODS = ["client_secret_basic", "client_secret_post"]

    def validate_token_request(self):
        if self.request.payload.scope:
            raise InvalidScopeError("Scopes act on a user's data; the client credentials grant has no user.")
        super().validate_token_request()


class TokenRevocationEndpoint(RevocationEndpoint):
    CLIENT_AUTH_METHODS = PUBLIC_AND_CONFIDENTIAL_AUTH_METHODS

    def query_token(self, token_string, token_type_hint):
        hashed = secret_hash(token_string)
        if token_type_hint != "refresh_token":
            token = OAuthToken.query.filter_by(access_token_hash=hashed).first()
            if token or token_type_hint == "access_token":
                return token
        return OAuthToken.query.filter_by(refresh_token_hash=hashed).first()

    def revoke_token(self, token, oauth_request):
        token.access_token_revoked = True
        if oauth_request.form.get("token_type_hint") != "access_token":
            token.refresh_token_revoked = True
        db.session.commit()


class _TokenValidator(BearerTokenValidator):
    def authenticate_token(self, token_string):
        return OAuthToken.query.filter_by(access_token_hash=secret_hash(token_string)).first()


_token_validator = _TokenValidator()


class _OpenIDToken(OpenIDCode):
    """Adds an ID token when the granted scope includes ``openid``."""

    def exists_nonce(self, nonce, oauth_request):
        from app.utils.oidc import nonce_was_used

        return nonce_was_used(nonce, oauth_request)

    def get_jwt_config(self, grant):
        from app.utils.oidc import jwt_config

        return jwt_config()

    def generate_user_info(self, user, scope):
        from app.utils.oidc import identity_claims

        return identity_claims(user, scope)


class _OpenIDRefresh(OpenIDToken):
    """Issues a fresh ID token when a refresh grant still includes ``openid``."""

    def get_jwt_config(self, grant):
        from app.utils.oidc import jwt_config

        return jwt_config()

    def generate_user_info(self, user, scope):
        from app.utils.oidc import identity_claims

        return identity_claims(user, scope)

    def __call__(self, grant):
        grant.register_hook("after_create_token_response", self.process_token)


def _validate_requested_scope(scope):
    if scope:
        scope = " ".join(part for part in scope.split() if part not in IGNORED_SCOPES)
    if scope and authorization_server.scopes_supported:
        if not set(authorization_server.scopes_supported).issuperset(scope.split()):
            raise InvalidScopeError()


def init_oauth_server(app) -> None:
    """Configure the authorization server on *app*."""
    app.config.setdefault("OAUTH2_REFRESH_TOKEN_GENERATOR", True)
    app.config.setdefault(
        "OAUTH2_TOKEN_EXPIRES_IN",
        {
            grant: ACCESS_TOKEN_LIFETIME_SECONDS
            for grant in ("authorization_code", "refresh_token", "client_credentials")
        },
    )
    app.config.setdefault("OAUTH2_SCOPES_SUPPORTED", list(SCOPES))
    authorization_server.init_app(app, query_client=_query_client, save_token=_save_token)
    authorization_server.validate_requested_scope = _validate_requested_scope
    authorization_server.register_grant(AuthorizationCodeGrant, [S256CodeChallenge(required=True), _OpenIDToken()])
    authorization_server.register_grant(RefreshTokenGrant, [_OpenIDRefresh()])
    authorization_server.register_grant(ClientCredentialsGrant)
    authorization_server.register_endpoint(TokenRevocationEndpoint)


def require_bearer(scope: str | None = None, user_required: bool = False):
    """Accept an OAuth access token on this route.

    Sets ``g.oauth_token`` to the validated :class:`~app.models.oauth.OAuthToken`
    and ``g.oauth_user`` to its Player/Team (``None`` for client-credentials
    tokens). It does not touch ``current_user``: routes pass ``g.oauth_user``
    to services explicitly, so the session-based permission paths never
    widen to bearer tokens by accident.

    Args:
        scope: Scope the token must carry, if any.
        user_required: Reject tokens that do not act on behalf of a user.
    """

    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            token_string = _bearer_token_string()
            token = _token_validator.authenticate_token(token_string) if token_string else None
            try:
                _token_validator.validate_token(token, [scope] if scope else None, request)
                user = token.get_user()
                if token.user_id is not None and user is None:
                    raise InvalidTokenError(description="The token's user no longer exists.")
                if user_required and user is None:
                    raise InsufficientScopeError(description="This endpoint needs a token issued for a user.")
            except (InvalidTokenError, InsufficientScopeError) as error:
                raise_http_exception(error.status_code, json.dumps(dict(error.get_body())), error.get_headers())
            now = now_utc_naive()
            if token.last_used_at is None or (now - token.last_used_at).total_seconds() > LAST_USED_RESOLUTION_SECONDS:
                token.last_used_at = now
                db.session.commit()
            g.oauth_token = token
            g.oauth_user = user
            return view(*args, **kwargs)

        return wrapper

    return decorator


def _bearer_token_string() -> str | None:
    authorization = request.headers.get("Authorization", "")
    kind, _, value = authorization.partition(" ")
    return value.strip() if kind.lower() == "bearer" and value.strip() else None
