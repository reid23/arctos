"""Serializer for the current authenticated user.

The ``user_json`` helper returns the minimal JSON-safe dictionary used
by ``/me``, ``/login``, ``/register``, and the Google OAuth completion
endpoints. The dict shape is part of the API contract:

    {
        "id":           str,
        "name":         str,
        "type":         "player" | "team",
        "profile_photo": str | None,
        "has_password": bool,
        "managed_teams": [{"id": str, "name": str, "profile_photo": str | None}],
    }

When the current user is not authenticated, returns ``None``.
"""

from __future__ import annotations

from flask_login import current_user

from app.services._common import current_user_type


def user_json() -> dict | None:
    """Serialise the current user to a minimal JSON-safe dictionary.

    Returns:
        A dict with keys ``id``, ``name``, ``type`` (``"player"`` or
        ``"team"``), ``profile_photo``, ``has_password``, and ``managed_teams``; or ``None`` when no user is
        authenticated.
    """
    if not current_user.is_authenticated:
        return None
    t = current_user_type()
    has_password = bool(getattr(current_user, "pw_hash", None))
    from app.services.permission_service import PermissionService

    return {
        "managed_teams": [
            {"id": team.id, "name": team.name, "profile_photo": team.profile_photo}
            for team in PermissionService.managed_teams(current_user)
        ],
        "id": current_user.id,
        "name": current_user.name,
        "type": t,
        "profile_photo": current_user.profile_photo,
        "has_password": has_password,
    }
