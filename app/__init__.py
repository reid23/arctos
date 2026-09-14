"""
Tournament site Flask application factory.
"""

import logging
from decimal import Decimal

from flask import Flask
from flask.json.provider import DefaultJSONProvider
from flask_login import LoginManager
import os

logger = logging.getLogger(__name__)


class ArctosJSONProvider(DefaultJSONProvider):
    """JSON provider that serialises ``Decimal`` as a JSON number.

    The SQLAlchemy ``Numeric(10, 2)`` columns (registration fees, paid
    amounts) come back as :class:`decimal.Decimal`. Flask's default
    provider emits those as JSON strings (e.g. ``"0.00"``), but every
    SPA consumer of those fields expects a JSON number. Convert here
    rather than at every callsite.
    """

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


# Initialize extensions (will be initialized in create_app)
db = None
login_manager = LoginManager()

# Override url_for to handle subpath deployment
from flask import url_for as _url_for


def url_for(endpoint: str, **values) -> str:
    """Return a URL for *endpoint*, prepending ``SCRIPT_NAME`` for subpath deployments.

    Wraps Flask's :func:`~flask.url_for` so that the URL includes the
    ``SCRIPT_NAME`` environment variable prefix when the application is
    served at a non-root path (e.g. behind a reverse proxy).

    Args:
        endpoint: The name of the Flask endpoint.
        **values: Values passed directly to :func:`~flask.url_for`.

    Returns:
        The generated URL, possibly prefixed with ``SCRIPT_NAME``.
    """
    url = _url_for(endpoint, **values)
    if "SCRIPT_NAME" in os.environ and not url.startswith(os.environ["SCRIPT_NAME"]):
        url = os.environ["SCRIPT_NAME"] + url
    return url


def create_app(config: dict | None = None) -> Flask:
    """Create and configure the Arctos Flask application.

    Wires together SQLAlchemy, Flask-Login, OAuth (Google), CORS (dev mode),
    all route blueprints, error handlers, Jinja filters, and Executor.  Also
    triggers a boot-time schedule recomputation and resumes any interrupted
    YouTube uploads when configured to do so.

    Args:
        config: Optional dict of configuration overrides.  Currently
            supports ``"SQLALCHEMY_DATABASE_URI"`` for test isolation.

    Returns:
        A fully configured :class:`~flask.Flask` application instance.
    """
    global db

    app = Flask(__name__, static_folder="../static", template_folder="../templates")
    app.json = ArctosJSONProvider(app)

    from app.utils.logging import get_or_configure_logger

    _log_level = os.environ.get("ARCTOS_LOG_LEVEL", "INFO")
    get_or_configure_logger(
        "root",
        logger=logging.getLogger(),
        log_level=_log_level,
        replace_handler=True,
    )
    get_or_configure_logger(
        app.logger.name,
        logger=app.logger,
        log_level=_log_level,
        replace_handler=True,
        propagate=False,
    )

    config = config or dict()
    # Default configuration
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key")
    app.config["SQLALCHEMY_DATABASE_URI"] = config.get("SQLALCHEMY_DATABASE_URI", "sqlite:///tournament.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # Record / footage uploads use multi-MB POST bodies; set explicitly so Werkzeug does not reject large chunks.
    # Reverse proxies (nginx client_max_body_size, etc.) may still need raising in deployment.
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH_BYTES", str(100 * 1024 * 1024)))
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    # For cross-origin SPA (e.g. dx serve on port 8080, Flask on 5006), set ARCTOS_CORS_DEV=1
    # so the session cookie is sent with credentialed requests. SameSite=None requires Secure
    # in production; on localhost some browsers allow it over HTTP.
    if os.environ.get("ARCTOS_CORS_DEV") == "1":
        app.config["SESSION_COOKIE_SAMESITE"] = "None"
        app.config["SESSION_COOKIE_SECURE"] = True
    else:
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # Google OAuth configuration
    app.config["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID", "")
    app.config["GOOGLE_CLIENT_SECRET"] = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    # Handle subpath deployment
    if "SCRIPT_NAME" in os.environ:
        app.config["APPLICATION_ROOT"] = os.environ["SCRIPT_NAME"]

    # Override with custom config if provided
    if config:
        app.config.update(config)

    # SQLite: increase busy timeout; finalize workers and HTTP handlers share one file.
    # The same timeout is also re-asserted via PRAGMA in `set_sqlite_pragmas` below so
    # that every new DBAPI connection gets it regardless of how the engine was built.
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if isinstance(uri, str) and uri.startswith("sqlite"):
        opts = dict(app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {})
        conn_args = dict(opts.get("connect_args") or {})
        conn_args.setdefault("timeout", 30)
        opts["connect_args"] = conn_args
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = opts

    # Initialize OAuth and Executor (after config is finalized)
    from app.routes.auth import oauth

    oauth.init_app(app)
    from app.routes.tournaments import executor

    executor.init_app(app)
    # Register Google OAuth client
    if app.config.get("GOOGLE_CLIENT_ID") and app.config.get("GOOGLE_CLIENT_SECRET"):
        oauth.register(
            name="google",
            client_id=app.config["GOOGLE_CLIENT_ID"],
            client_secret=app.config["GOOGLE_CLIENT_SECRET"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    # Initialize database
    from models import db as db_instance, init_db

    db = db_instance
    db.init_app(app)
    init_db(db)
    # Per-connection SQLite pragmas. WAL + busy_timeout let readers/writers interleave
    # better than the default rollback journal; foreign-key enforcement is added here
    # because SQLite ships with it OFF by default and only honours it when the pragma
    # is set on EVERY new connection.
    try:
        with app.app_context():
            eng = db.engine
            if eng.dialect.name == "sqlite":
                from sqlalchemy import event

                @event.listens_for(eng, "connect")
                def set_sqlite_pragmas(dbapi_connection, _connection_record):
                    """Apply Arctos-required SQLite pragmas to a fresh DBAPI connection.

                    Runs once per new connection that SQLAlchemy's pool hands out:

                    * ``journal_mode=WAL`` — readers and writers do not block each
                      other; required because finalize workers and HTTP handlers
                      share a single database file.
                    * ``busy_timeout=30000`` — wait up to 30 s for a competing writer
                      instead of failing immediately with ``SQLITE_BUSY``.
                    * ``foreign_keys=ON`` — SQLite disables FK enforcement by default
                      and only checks it when this pragma is set on the connection.
                      Without this every ``ForeignKey`` in the schema is decorative
                      and deletes silently orphan child rows. This is the one
                      non-obvious pragma; do not remove it.
                    """
                    cur = dbapi_connection.cursor()
                    try:
                        cur.execute("PRAGMA journal_mode=WAL")
                        cur.execute("PRAGMA busy_timeout=30000")
                        cur.execute("PRAGMA foreign_keys=ON")
                    finally:
                        cur.close()

    except Exception:
        logger.exception("Failed to register SQLite pragma listener")

    # Initialize login manager
    login_manager.init_app(app)
    login_manager.login_view = "auth.login_redirect"

    @login_manager.unauthorized_handler
    def unauthorized():
        from flask import request, redirect, url_for, jsonify

        # For _api routes, return 401 JSON so the SPA gets a proper response instead of
        # a redirect to /login (which would cause CORS errors when the browser follows it).
        if request.path.startswith("/_api"):
            return jsonify({"error": "Not authenticated"}), 401
        return redirect(url_for(login_manager.login_view, next=request.url))

    @login_manager.user_loader
    def load_user(user_id):
        from models import Player, Team

        # Try to load as player first, then team
        user = Player.query.get(user_id)
        if user:
            return user
        return Team.query.get(user_id)

    # Register blueprints (all API under /_api/; nginx serves frontend at root)
    from app.routes.auth import bp as auth_bp
    from app.routes.tournaments import bp as tournaments_bp
    from app.routes.matches import bp as matches_bp
    from app.routes.notes import bp as notes_bp
    from app.routes.registration import bp as registration_bp
    from app.routes.sidecomps import bp as sidecomps_bp
    from app.routes.content import bp as content_bp
    from app.routes.leagues import bp as leagues_bp
    from app.routes.penalty_types import bp as penalty_types_bp
    from app.routes.players import bp as players_bp
    from app.routes.teams import bp as teams_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(tournaments_bp)
    app.register_blueprint(matches_bp)
    app.register_blueprint(notes_bp)
    app.register_blueprint(registration_bp)
    app.register_blueprint(sidecomps_bp)
    app.register_blueprint(content_bp)
    app.register_blueprint(leagues_bp)
    app.register_blueprint(penalty_types_bp)
    app.register_blueprint(players_bp)
    app.register_blueprint(teams_bp)

    # Register template filters
    from app import filters

    app.register_blueprint(filters.bp)

    # Make custom url_for available in templates
    @app.context_processor
    def inject_url_for():
        return dict(url_for=url_for)

    # CORS for /_api when using dx serve (frontend on different port/protocol than Flask)
    def _cors_allowed_origin(origin_header):
        if not origin_header:
            return None
        origin_lower = origin_header.strip().lower()
        if "localhost" in origin_lower or "127.0.0.1" in origin_lower:
            return origin_header.strip()
        return None

    def _add_cors_headers(response_or_headers, origin):
        if hasattr(response_or_headers, "headers"):
            h = response_or_headers.headers
        else:
            h = response_or_headers
        h["Access-Control-Allow-Origin"] = origin
        h["Access-Control-Allow-Credentials"] = "true"
        h["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        h["Access-Control-Allow-Headers"] = "Content-Type, Authorization, Accept"
        h["Vary"] = "Origin"

    @app.after_request
    def add_cors_for_api(response):
        from flask import request

        # When ARCTOS_CORS_DEV=1, add CORS for all requests (any path with Origin).
        # Otherwise only for /_api and (in dev) /static/
        cors_dev_all = os.environ.get("ARCTOS_CORS_DEV") == "1"
        is_api = "/_api" in request.path
        is_static_cors = cors_dev_all and request.endpoint == "static" and request.path.startswith("/static/")
        if not cors_dev_all and not is_api and not is_static_cors:
            return response
        origin_header = request.headers.get("Origin")
        origin = _cors_allowed_origin(origin_header) if origin_header else None
        if origin:
            _add_cors_headers(response, origin)
        return response

    @app.before_request
    def handle_api_preflight():
        from flask import request, make_response

        # When ARCTOS_CORS_DEV=1, handle OPTIONS preflight for any path.
        # Otherwise only for /_api and (in dev) /static/
        cors_dev_all = os.environ.get("ARCTOS_CORS_DEV") == "1"
        is_api = "/_api" in request.path
        is_static_cors = cors_dev_all and request.path.startswith("/static/")
        if request.method != "OPTIONS" or (not cors_dev_all and not is_api and not is_static_cors):
            return None
        origin_header = request.headers.get("Origin")
        origin = _cors_allowed_origin(origin_header) if origin_header else None
        r = make_response("", 204)
        if origin:
            _add_cors_headers(r, origin)
        return r

    # Add cache headers to static file responses (especially images)
    @app.after_request
    def add_cache_headers(response):
        from flask import request

        # Check if this is a static file request
        if response.status_code == 200 and request.endpoint == "static":
            # Cache images and other static assets for 1 hour
            if request.path.startswith("/static/uploads/") or request.path.startswith("/static/"):
                response.cache_control.max_age = 3600
                response.cache_control.public = True
        return response

    # Error handlers
    from app.error_handlers import register_error_handlers

    register_error_handlers(app)

    # On boot: recompute schedule for all tournaments that are not complete (end_date in future or None)
    try:
        with app.app_context():
            from datetime import datetime, timezone
            from models import Tournament
            from app.utils.scheduling import recompute_scheduled_and_nominal_times

            now = datetime.now(timezone.utc)
            # Materialise the (url, end_date) pairs up front so iteration is
            # decoupled from the session: recompute_scheduled_and_nominal_times commits in
            # the global session, which expires every still-pending ORM row in
            # the iterator and would otherwise raise DetachedInstanceError on
            # the next access.
            tournaments = [(t.url, t.end_date) for t in Tournament.query.all()]
            db.session.remove()
            for url, end_date in tournaments:
                if end_date is None:
                    not_complete = True
                else:
                    end_utc = end_date.replace(tzinfo=timezone.utc) if end_date.tzinfo is None else end_date
                    not_complete = end_utc >= now
                if not_complete:
                    try:
                        recompute_scheduled_and_nominal_times(url)
                    except Exception:
                        logger.exception("recompute_scheduled_and_nominal_times failed for tournament %s", url)
    except Exception:
        logger.exception("Tournament boot-time recompute pass failed")

    # On boot: resume any YouTube uploads that were left in-progress before a restart.
    # This is best-effort and only runs outside of tests.
    try:
        if not app.config.get("TESTING", False):
            from models import Camera
            import threading

            # If YouTube upload is not configured, uploading would immediately mark them FAILED.
            # Guard early to avoid churn.
            if os.environ.get("YOUTUBE_UPLOAD_REFRESH_TOKEN", "").strip():
                from app.utils.youtube_upload import upload_camera_to_youtube

                with app.app_context():
                    in_progress = Camera.query.filter_by(status="UPLOADING").all()
                    if in_progress:
                        app_obj = app._get_current_object()

                        def _resume(uuid: str) -> None:
                            with app_obj.app_context():
                                upload_camera_to_youtube(uuid)

                        for cam in in_progress:
                            threading.Thread(
                                target=_resume,
                                args=(str(cam.uuid),),
                                daemon=True,
                            ).start()
    except Exception:
        logger.exception("YouTube upload resume failed")

    @app.errorhandler(413)
    def too_large(e):
        from flask import jsonify

        return jsonify({"success": False, "error": "File too large."}), 413

    return app
