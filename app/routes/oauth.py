"""OAuth 2.0 and OpenID Connect provider routes ("Sign in with Arctos").

Hosts the ``oauth`` blueprint. Two audiences:

* Third-party apps (stable, public contract under ``/api/oauth``):
  ``POST /api/oauth/token``, ``POST /api/oauth/revoke``, and
  ``GET /api/oauth/userinfo``. The authorization endpoint apps send users
  to is the SPA page ``/oauth/authorize``.
* The SPA (internal, under ``/_api/oauth``): consent, app registration,
  and connected-app management for the signed-in user.

Protocol logic lives in :mod:`app.utils.oauth_server`; client and
connected-app management in :mod:`app.services.oauth_service`.
"""

from __future__ import annotations

from authlib.oauth2 import OAuth2Error
from authlib.oidc.core.errors import ConsentRequiredError, LoginRequiredError
from flask import Blueprint, g, jsonify, request
from flask_login import current_user, login_required

from app.error_values import Ok
from app.services._common import current_user_type
from app.services.oauth_service import OAuthService
from app.utils.datetime_helpers import to_iso_z
from app.utils.decorators import require_json_body
from app.utils.oauth_server import SCOPES, authorization_server, require_bearer
from app.utils.oidc import json_web_key, provider_metadata, userinfo_document
from app.utils.responses import json_error
from app.utils.result_helpers import json_from_result

bp = Blueprint("oauth", __name__)


def _client_json(client) -> dict:
    return {
        "client_id": client.client_id,
        "name": client.name,
        "homepage_url": client.homepage_url,
        "redirect_uris": client.redirect_uri_list,
        "confidential": not client.is_public,
        "created_at": to_iso_z(client.created_at).unwrap_or(None),
    }


@bp.route("/api/oauth/token", methods=["POST"])
def token():
    """Token endpoint (RFC 6749 section 3.2): exchange a code, refresh, or client credentials."""
    return authorization_server.create_token_response()


@bp.route("/api/oauth/revoke", methods=["POST"])
def revoke():
    """Revocation endpoint (RFC 7009)."""
    return authorization_server.create_endpoint_response("revocation")


@bp.route("/api/oauth/userinfo", methods=["GET", "POST"])
@require_bearer(user_required=True)
def userinfo():
    """Who the access token acts for.

    Always includes the OpenID ``sub`` plus the historical ``id``, ``name``,
    and ``type`` fields. ``picture`` is added for the profile scope.
    """
    user = g.oauth_user
    return jsonify(userinfo_document(user, g.oauth_token.user_type, g.oauth_token.scope or ""))


@bp.route("/.well-known/openid-configuration")
def openid_configuration():
    """OpenID Connect discovery document."""
    return jsonify(provider_metadata())


@bp.route("/api/oauth/jwks")
def jwks():
    """Public signing keys for ID tokens."""
    return jsonify({"keys": [json_web_key()]})


def _authorization_outcome(response):
    """Turn Authlib's authorization response into JSON the SPA can act on.

    Authlib answers with a redirect to the client (success, denial, or an
    error the client should see) or with an error body when the client or
    redirect URI itself is invalid and must not be redirected to.
    """
    if response.status_code == 302:
        return jsonify({"redirect": response.headers["Location"]})
    body = response.get_json(silent=True) or {}
    return json_error(body.get("error_description") or body.get("error") or "Invalid authorization request")


def _prompt_values() -> list[str]:
    return request.args.get("prompt", "").split()


def _signed_in_user():
    return current_user if current_user.is_authenticated else None


@bp.route("/_api/oauth/authorize")
def authorization_request():
    """Validate an authorization request and describe it for the consent page.

    Takes the authorization request's query parameters unchanged. ``prompt=none``
    never returns a page: it redirects with a code when this account already
    allowed every requested scope, and otherwise with ``login_required`` or
    ``consent_required``.
    """
    user = _signed_in_user()
    prompts = _prompt_values()
    if user is None and "none" not in prompts:
        return json_error("Sign in to continue.", status_code=401)
    try:
        grant = authorization_server.get_consent_grant(end_user=user)
    except OAuth2Error as error:
        return _authorization_outcome(authorization_server.handle_error_response(None, error))
    if user is None:
        error = LoginRequiredError(redirect_uri=grant.redirect_uri, state=request.args.get("state"))
        return _authorization_outcome(authorization_server.handle_error_response(None, error))
    client = grant.client
    requested = [scope for scope in (grant.request.payload.scope or "").split() if scope in SCOPES]
    already_allowed = "consent" not in prompts and OAuthService.grant_covers(
        user.id, current_user_type(), client.client_id, requested
    )
    if "none" in prompts:
        if not already_allowed:
            error = ConsentRequiredError(redirect_uri=grant.redirect_uri, state=request.args.get("state"))
            return _authorization_outcome(authorization_server.handle_error_response(None, error))
        return _authorization_outcome(
            authorization_server.create_authorization_response(grant_user=user._get_current_object(), grant=grant)
        )
    return jsonify(
        {
            "client": {
                "client_id": client.client_id,
                "name": client.name,
                "homepage_url": client.homepage_url,
                "owner_id": client.owner_id,
            },
            "scopes": [{"name": scope, "description": SCOPES[scope]} for scope in requested],
            "user": {"id": user.id, "name": user.name},
            "already_allowed": already_allowed,
        }
    )


@bp.route("/_api/oauth/authorize", methods=["POST"])
@login_required
@require_json_body()
def authorization_decision():
    """Approve or deny an authorization request.

    Query string: the original authorization request. JSON body:
    ``{"approve": bool}``. Returns ``{"redirect": url}`` for the SPA to
    navigate to.
    """
    try:
        grant = authorization_server.get_consent_grant(end_user=current_user)
    except OAuth2Error as error:
        return _authorization_outcome(authorization_server.handle_error_response(None, error))
    user = current_user._get_current_object() if g.json_body.get("approve") is True else None
    return _authorization_outcome(authorization_server.create_authorization_response(grant_user=user, grant=grant))


@bp.route("/_api/oauth/clients")
@login_required
def list_clients():
    clients = OAuthService.list_clients(current_user.id, current_user_type())
    return jsonify({"clients": [_client_json(client) for client in clients]})


@bp.route("/_api/oauth/clients", methods=["POST"])
@login_required
@require_json_body()
def create_client():
    """Register an app. JSON: ``{name, homepage_url?, redirect_uris: [..], confidential: bool}``.

    The response carries ``client_secret`` for confidential apps; it is
    not stored in plaintext and cannot be shown again.
    """
    body = g.json_body
    result = OAuthService.create_client(
        current_user.id,
        current_user_type(),
        body.get("name", ""),
        body.get("homepage_url"),
        body.get("redirect_uris") or [],
        bool(body.get("confidential")),
    )
    return json_from_result(
        result,
        ok_to_payload=lambda created: {"client": _client_json(created[0]), "client_secret": created[1]},
    )


@bp.route("/_api/oauth/clients/<client_id>", methods=["PUT"])
@login_required
@require_json_body()
def update_client(client_id):
    body = g.json_body
    match OAuthService.owned_client(current_user.id, current_user_type(), client_id):
        case Ok(client):
            result = OAuthService.update_client(
                client, body.get("name", ""), body.get("homepage_url"), body.get("redirect_uris") or []
            )
        case error:
            result = error
    return json_from_result(result, ok_to_payload=lambda client: {"client": _client_json(client)})


@bp.route("/_api/oauth/clients/<client_id>/secret", methods=["POST"])
@login_required
def rotate_client_secret(client_id):
    match OAuthService.owned_client(current_user.id, current_user_type(), client_id):
        case Ok(client):
            result = OAuthService.rotate_secret(client)
        case error:
            result = error
    return json_from_result(result, ok_to_payload=lambda secret: {"client_secret": secret})


@bp.route("/_api/oauth/clients/<client_id>", methods=["DELETE"])
@login_required
def delete_client(client_id):
    result = OAuthService.owned_client(current_user.id, current_user_type(), client_id)
    if result.is_ok():
        OAuthService.delete_client(result.unwrap())
    return json_from_result(result, ok_to_payload=lambda _: {})


@bp.route("/_api/oauth/connected-apps")
@login_required
def connected_apps():
    """Apps the signed-in user has authorized and not disconnected."""
    apps = OAuthService.connected_apps(current_user.id, current_user_type())
    return jsonify(
        {
            "apps": [
                {
                    "client_id": app.client.client_id,
                    "name": app.client.name,
                    "homepage_url": app.client.homepage_url,
                    "scopes": [{"name": scope, "description": SCOPES[scope]} for scope in app.scopes],
                    "last_used_at": to_iso_z(app.last_used_at).unwrap_or(None),
                }
                for app in apps
            ]
        }
    )


@bp.route("/_api/oauth/connected-apps/<client_id>", methods=["DELETE"])
@login_required
def disconnect_app(client_id):
    """Revoke every token the app holds for the signed-in user."""
    OAuthService.disconnect(current_user.id, current_user_type(), client_id)
    return jsonify({"success": True})
