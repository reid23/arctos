"""OpenID Connect identity layer for the OAuth authorization server.

Issues RS256 ID tokens, publishes the signing key, and describes the
provider at ``/.well-known/openid-configuration``. An ID token is only
minted when the grant includes the ``openid`` scope; other grants stay
plain OAuth 2.0.
"""

from __future__ import annotations

import secrets

from authlib.jose import JsonWebKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import current_app, request
from sqlalchemy.exc import IntegrityError

from app.models.base import db
from app.models.oauth import OAuthAuthorizationCode, OidcSigningKey

ID_TOKEN_LIFETIME_SECONDS = 3600


def issuer() -> str:
    """Issuer identifier, with no trailing slash.

    ``ARCTOS_PUBLIC_ORIGIN`` wins so tokens keep a stable ``iss`` behind a
    proxy. Otherwise the current request's origin is used.
    """
    origin = (current_app.config.get("PUBLIC_CANONICAL_ORIGIN") or "").rstrip("/")
    if origin:
        return origin
    return request.host_url.rstrip("/")


def _generate_key() -> OidcSigningKey:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return OidcSigningKey(id=1, kid=secrets.token_hex(8), private_pem=pem)


def signing_key() -> OidcSigningKey:
    """Return the singleton signing key, creating it on first use."""
    row = db.session.get(OidcSigningKey, 1)
    if row is not None:
        return row
    row = _generate_key()
    try:
        db.session.add(row)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        row = db.session.get(OidcSigningKey, 1)
    return row


def json_web_key() -> dict:
    """Public JWK for the signing key."""
    key = signing_key()
    imported = JsonWebKey.import_key(key.private_pem, {"kty": "RSA", "kid": key.kid, "use": "sig", "alg": "RS256"})
    return imported.as_dict(is_private=False)


def jwt_config() -> dict:
    key = signing_key()
    return {
        "key": key.private_pem,
        "alg": "RS256",
        "iss": issuer(),
        "exp": ID_TOKEN_LIFETIME_SECONDS,
        "kid": key.kid,
    }


def picture_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{issuer()}{path}"
    return f"{issuer()}/static/{path.lstrip('/')}"


def identity_claims(user, scope: str) -> dict:
    """Claims the account has agreed to release for *scope*.

    Name and username are released to every user grant, matching the
    consent screen. A profile photo requires the profile scope. The
    ``email`` claim is the account id from the profile URL, not a mailbox
    and not the account's contact address.
    """
    granted = set(scope.split())
    claims = {"sub": user.id, "name": user.name, "preferred_username": user.id}
    if "email" in granted:
        claims["email"] = user.id
    if "profile" in granted:
        picture = picture_url(getattr(user, "profile_photo", None))
        if picture:
            claims["picture"] = picture
    return claims


def userinfo_document(user, user_type: str, scope: str) -> dict:
    """UserInfo response: OpenID claims plus the existing ``id`` and ``type`` fields."""
    document = identity_claims(user, scope)
    document["id"] = user.id
    document["type"] = user_type
    return document


def provider_metadata() -> dict:
    base = issuer()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/api/oauth/token",
        "userinfo_endpoint": f"{base}/api/oauth/userinfo",
        "jwks_uri": f"{base}/api/oauth/jwks",
        "revocation_endpoint": f"{base}/api/oauth/revoke",
        "scopes_supported": ["openid", "profile", "email", "injuries", "footage"],
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "claims_supported": ["sub", "name", "preferred_username", "picture", "email"],
        "service_documentation": f"{base}/developers.md",
    }


def nonce_was_used(nonce: str, oauth_request) -> bool:
    """Whether *nonce* is already on an unexpired code for this client."""
    if not nonce:
        return False
    existing = OAuthAuthorizationCode.query.filter_by(client_id=oauth_request.payload.client_id, nonce=nonce).first()
    return existing is not None and not existing.is_expired()
