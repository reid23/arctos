from flask import g

from models import RegistrableConfig, db


def make_registrable_config(**kwargs) -> RegistrableConfig:
    """Create a RegistrableConfig with defaults suitable for testing.

    Must be called within an active app context (e.g. inside a test_db scope).
    """
    defaults = {
        "team_registration_open": False,
        "player_registration_open": False,
    }
    defaults.update(kwargs)
    cfg = RegistrableConfig(**defaults)
    db.session.add(cfg)
    db.session.flush()
    return cfg


def login_as(client, user):
    """
    Log in a user for tests using Flask-Login's session keys.

    Requests share the test's app context, so Flask-Login's per-context user
    cache is cleared to let later requests load the new user.
    """
    with client.session_transaction() as sess:
        sess["_user_id"] = user.get_id()
        sess["_fresh"] = True
    g.pop("_login_user", None)


def issue_access_token(user=None, scope: str = "", client_name: str = "Test App") -> str:
    """Register an OAuth client and insert an access token for it; return the token string.

    *user* is the Player/Team the token acts for, or ``None`` for a
    client-credentials token. *scope* is space-separated.
    """
    import secrets

    from app.models.oauth import OAuthClient, OAuthToken, secret_hash
    from app.services.permission_service import PermissionService

    user_type = str(PermissionService.user_type(user).unwrap()) if user else None
    client = OAuthClient(
        client_id=secrets.token_hex(8),
        client_secret_hash=secret_hash("client-secret"),
        name=client_name,
        redirect_uris="https://app.example/callback",
        owner_id=user.id if user else "app-owner",
        owner_type=user_type or "player",
    )
    token = secrets.token_urlsafe(24)
    db.session.add(client)
    db.session.add(
        OAuthToken(
            client_id=client.client_id,
            user_id=user.id if user else None,
            user_type=user_type,
            scope=scope,
            access_token_hash=secret_hash(token),
            expires_in=3600,
        )
    )
    db.session.commit()
    return token
