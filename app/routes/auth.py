"""
Authentication routes: logout, check-username, Google OAuth (login + callback only).
Login and register are handled by the SPA and _api.
"""

from urllib.parse import urlsplit

from flask import (
    Blueprint,
    request,
    redirect,
    flash,
    jsonify,
    session,
    current_app,
    url_for,
    g,
)
from flask_login import current_user, login_user, logout_user, login_required
from models import Player, Team, db
from app.serializers.user_serializer import user_json
from app.utils.decorators import require_json_body
from app.utils.helpers import is_valid_url_username
from authlib.integrations.flask_client import OAuth

bp = Blueprint("auth", __name__, url_prefix="/_api")

# Initialize OAuth (will be configured in app factory)
oauth = OAuth()

_SPA_BASE = "/"


def _frontend_path(path: str = "") -> str:
    """Return a same-host frontend path, preserving any deployment subpath."""

    base = request.script_root.rstrip("/") or _SPA_BASE.rstrip("/")
    suffix = f"/{path.lstrip('/')}" if path else ""
    return f"{base}{suffix}" or _SPA_BASE


def _login_persistent(user):
    """Sign in and keep the session after the browser closes.

    A non-permanent session cookie is dropped whenever the browser discards
    session cookies (tab close, mobile reclaim, short inactivity). The
    permanent session and Flask-Login remember cookie last a year.
    """
    session.permanent = True
    login_user(user, remember=True)


def _redirect_to_frontend(path: str = ""):
    """Build a Flask redirect response targeting the frontend SPA.

    Args:
        path: Frontend path to append, should begin with ``/``
            (e.g. ``"/auth/google/choose-account-type"``).

    Returns:
        A Flask :func:`~flask.redirect` response.
    """
    return redirect(_frontend_path(path))


def _google_callback_uri() -> str:
    """Return the Google OAuth callback URI registered in Google Cloud Console.

    An explicit URI supports development proxies that replace the Host header.
    Without one, derive the callback from the request to support multiple public
    domains. Each callback URI must be authorized in Google Cloud Console.

    Returns:
        Absolute callback URL string.
    """
    configured = current_app.config.get("GOOGLE_REDIRECT_URI", "")
    if configured:
        error = "GOOGLE_REDIRECT_URI must be an absolute HTTP(S) callback URL without credentials, query, or fragment"
        if not isinstance(configured, str) or any(c.isspace() or ord(c) < 32 for c in configured) or "\\" in configured:
            raise ValueError(error)
        try:
            parsed = urlsplit(configured)
            valid = (
                parsed.scheme in ("http", "https")
                and parsed.hostname
                and parsed.port != 0
                and parsed.username is None
                and parsed.password is None
                and "?" not in configured
                and "#" not in configured
            )
        except ValueError as exc:
            raise ValueError(error) from exc
        if not valid:
            raise ValueError(error)
        return configured
    return f"{request.url_root.rstrip('/')}{url_for('auth.google_callback')}"


@bp.route("/check-username", methods=["GET"])
def check_username():
    """Check username availability.

    ``GET /_api/check-username?username=<value>``

    Validates that the username is URL-safe and not already registered by
    any :class:`~app.models.user.Player` or :class:`~app.models.user.Team`.

    Query Args:
        username: The candidate username string.

    Returns:
        JSON ``{"available": bool, "message": str}``.
    """
    username = request.args.get("username", "")

    if not username:
        return jsonify({"available": False, "message": "Username is required"})

    if not is_valid_url_username(username):
        return jsonify(
            {
                "available": False,
                "message": "Username must be URL-safe: only letters, numbers, hyphens, and underscores. Cannot start or end with hyphen or underscore.",
            }
        )

    existing_player = Player.query.filter_by(id=username).first()
    existing_team = Team.query.filter_by(id=username).first()

    if existing_player or existing_team:
        return jsonify({"available": False, "message": "Username already exists"})

    return jsonify({"available": True, "message": "Username is available"})


@bp.route("/logout")
@login_required
def logout():
    """Log out the current user and redirect to the frontend home page.

    ``GET /_api/logout``

    Requires authentication.  Flashes a confirmation message after logout.

    Returns:
        A redirect response to the frontend root ``/``.
    """
    logout_user()
    flash("You have been logged out", "info")
    return _redirect_to_frontend("/")


@bp.route("/auth/google/login")
def google_login():
    """Initiate the Google OAuth 2.0 authorisation code flow.

    ``GET /_api/auth/google/login``

    Redirects the user to Google's consent screen. An optional same-site
    ``next`` path is where the user lands after signing in.  Returns an error flash
    and a redirect to the frontend if Google OAuth is not configured.

    Returns:
        A redirect response to Google's authorisation endpoint, or to the
        frontend home page on configuration error.
    """
    if not current_app.config.get("GOOGLE_CLIENT_ID") or not current_app.config.get("GOOGLE_CLIENT_SECRET"):
        flash(
            "Google sign-in is not configured. Please contact the administrator.",
            "error",
        )
        return _redirect_to_frontend("/")

    next_path = request.args.get("next", "")
    if next_path.startswith("/") and not next_path.startswith("//"):
        session["login_next"] = next_path
    else:
        session.pop("login_next", None)
    redirect_uri = _google_callback_uri()
    google = oauth.google
    return google.authorize_redirect(redirect_uri)


@bp.route("/auth/google/callback")
def google_callback():
    """Handle the Google OAuth 2.0 callback.

    ``GET /_api/auth/google/callback``

    Exchanges the authorisation code for tokens, fetches user info, and:

    * Logs in an existing account (player or team) if the ``google_id``
      is already registered.
    * Stores the Google profile in the session and redirects to the
      account-type selection page for new users.

    Returns:
        A redirect to the frontend home page on success or error, or to
        ``/auth/google/choose-account-type`` for new Google users.
    """
    try:
        google = oauth.google
        token = google.authorize_access_token()

        userinfo_endpoint = getattr(google, "server_metadata", {}).get("userinfo_endpoint")
        if not userinfo_endpoint:
            try:
                google.load_server_metadata()
                userinfo_endpoint = google.server_metadata.get("userinfo_endpoint")
            except Exception:
                userinfo_endpoint = None
        if not userinfo_endpoint:
            raise RuntimeError("Google userinfo endpoint not found in provider metadata")
        resp = google.get(userinfo_endpoint)
        user_info = resp.json()

        google_id = user_info.get("sub")
        email = user_info.get("email", "")
        name = user_info.get("name", email.split("@")[0] if email else "User")

        if not google_id:
            flash("Failed to authenticate with Google", "error")
            return _redirect_to_frontend("/")

        user = Player.query.filter_by(google_id=google_id).first()
        if not user:
            user = Team.query.filter_by(google_id=google_id).first()

        if user:
            _login_persistent(user)
            flash("Successfully logged in with Google!", "success")
            return _redirect_to_frontend(session.pop("login_next", "/"))

        session["google_oauth_data"] = {
            "google_id": google_id,
            "email": email,
            "name": name,
        }
        return _redirect_to_frontend("/auth/google/choose-account-type")

    except Exception as e:
        flash(f"Error during Google authentication: {str(e)}", "error")
        return _redirect_to_frontend("/")


@bp.route("/")
def login_redirect():
    """Redirect to SPA root (used as login_view when unauthenticated)."""
    return redirect("/")


@bp.route("/me", methods=["GET"])
def me():
    """Return current user or 401."""
    u = user_json()
    if u is None:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify(u)


@bp.route("/login", methods=["POST"])
@require_json_body()
def login():
    """JSON body: { username, password }. Sets session cookie on success."""
    data = g.json_body
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    user = Player.query.filter_by(id=username).first()
    if not user:
        user = Team.query.filter_by(id=username).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid username or password"}), 401
    _login_persistent(user)
    return jsonify(user_json())


@bp.route("/logout", methods=["POST"])
@login_required
def logout_post():
    """Clear session (JSON API for the SPA)."""
    logout_user()
    return jsonify({"ok": True})


@bp.route("/change-password", methods=["POST"])
@login_required
@require_json_body()
def change_password():
    """JSON body: { current_password, new_password }. Change authenticated user's password."""
    data = g.json_body
    current_password = data.get("current_password")
    new_password = data.get("new_password")
    if not current_password or not new_password:
        return jsonify({"error": "current_password and new_password required"}), 400
    user = current_user
    if not user.pw_hash:
        return (
            jsonify(
                {
                    "error": "This account uses Google sign-in. Password cannot be changed here.",
                }
            ),
            400,
        )
    if not user.check_password(current_password):
        return jsonify({"error": "Current password is incorrect"}), 401
    user.set_password(new_password)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/register", methods=["POST"])
@require_json_body()
def register():
    """JSON body: { username, password, name, user_type?: "player"|"team" }. Creates user and logs in."""
    data = g.json_body
    username = data.get("username")
    password = data.get("password")
    name = data.get("name")
    user_type = data.get("user_type", "player")
    if not username or not password or not name:
        return jsonify({"error": "username, password, and name required"}), 400
    if user_type not in ("player", "team"):
        return jsonify({"error": "user_type must be player or team"}), 400
    if not is_valid_url_username(username):
        return (
            jsonify(
                {
                    "error": "Username must be URL-safe: letters, numbers, hyphens, underscores. Cannot start or end with hyphen or underscore.",
                }
            ),
            400,
        )
    if Player.query.filter_by(id=username).first() or Team.query.filter_by(id=username).first():
        return jsonify({"error": "Username already exists"}), 409
    if user_type == "player":
        user = Player(id=username, name=name)
    else:
        user = Team(id=username, name=name)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    _login_persistent(user)
    return jsonify(user_json())


@bp.route("/google/choose-account-type", methods=["GET", "POST"])
def google_choose_account_type_api():
    """Select account type (player / team) after Google OAuth.

    ``GET  /_api/google/choose-account-type`` — Returns the email stored in the
    session so the frontend can pre-fill the form.

    ``POST /_api/google/choose-account-type`` — Stores the chosen
    ``user_type`` in the session and returns ``{"ok": true}``.

    Request JSON (POST):
        user_type (str): ``"player"`` or ``"team"``.

    Returns:
        JSON object or error with HTTP 400/401.
    """
    oauth_data = session.get("google_oauth_data")
    if not oauth_data:
        return jsonify({"error": "Session expired"}), 401

    if request.method == "POST":
        if not request.is_json:
            return jsonify({"error": "Content-Type must be application/json"}), 415
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400
        user_type = data.get("user_type")
        if user_type not in ["player", "team"]:
            return jsonify({"error": "Please select an account type"}), 400
        oauth_data["user_type"] = user_type
        session["google_oauth_data"] = oauth_data
        session.modified = True
        return jsonify({"ok": True})

    return jsonify({"email": oauth_data.get("email", "")})


@bp.route("/google/complete-profile", methods=["GET", "POST"])
def google_complete_profile_api():
    """Complete account creation for a new Google OAuth user.

    ``GET  /_api/google/complete-profile`` — Returns the email, account type,
    and a suggested display name derived from the session-stored OAuth data.

    ``POST /_api/google/complete-profile`` — Validates the chosen username and
    display name, creates the :class:`~app.models.user.Player` or
    :class:`~app.models.user.Team` record, clears the OAuth session data, and
    logs the user in.

    Request JSON (POST):
        username (str): Desired URL-safe username.
        display_name (str): Public display name.

    Returns:
        JSON object with ``ok`` key on success, or error with HTTP 400/401/409.
    """
    oauth_data = session.get("google_oauth_data")
    if not oauth_data:
        return jsonify({"error": "Session expired"}), 401

    user_type = oauth_data.get("user_type")
    if not user_type:
        return jsonify({"error": "Account type not selected"}), 400

    email = oauth_data.get("email", "")
    suggested_name = oauth_data.get("name", email.split("@")[0] if email else "User")

    if request.method == "POST":
        if not request.is_json:
            return jsonify({"error": "Content-Type must be application/json"}), 415
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400
        username = (data.get("username") or "").strip()
        display_name = (data.get("display_name") or "").strip()

        if not username:
            return jsonify({"error": "Username is required"}), 400
        if not is_valid_url_username(username):
            return (
                jsonify(
                    {
                        "error": "Username must be URL-safe: only letters, numbers, hyphens, and underscores. Cannot start or end with hyphen or underscore.",
                    }
                ),
                400,
            )
        existing_player = Player.query.filter_by(id=username).first()
        existing_team = Team.query.filter_by(id=username).first()
        if existing_player or existing_team:
            return jsonify({"error": "Username already exists"}), 409
        if not display_name:
            return jsonify({"error": "Display name is required"}), 400

        if user_type == "player":
            user = Player(
                id=username,
                name=display_name,
                google_id=oauth_data["google_id"],
                email=email,
                profile_photo=(None if username.lower() not in ("jeb", "jebediah") else "jeb.png"),
            )
        else:
            user = Team(
                id=username,
                name=display_name,
                google_id=oauth_data["google_id"],
                email=email,
                profile_photo=(None if username.lower() not in ("jeb", "jebediah") else "jeb.png"),
            )
        db.session.add(user)
        db.session.commit()
        session.pop("google_oauth_data", None)
        _login_persistent(user)
        return jsonify({"ok": True})

    return jsonify(
        {
            "email": email,
            "user_type": user_type,
            "suggested_name": suggested_name,
        }
    )
