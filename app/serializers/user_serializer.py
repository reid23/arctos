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
        ``"team"``), ``profile_photo``, and ``has_password``; or ``None`` when no user is
        authenticated.
    """
    if not current_user.is_authenticated:
        return None
    t = current_user_type()
    has_password = bool(getattr(current_user, "pw_hash", None))
    return {
        "id": current_user.id,
        "name": current_user.name,
        "type": t,
        "profile_photo": current_user.profile_photo,
        "has_password": has_password,
    }
