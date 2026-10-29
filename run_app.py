"""WSGI entry point for the Arctos Flask application.

Creates the application instance via the factory pattern and exposes it
as ``app`` for WSGI servers (gunicorn, uWSGI, etc.).  When invoked
directly it starts the built-in development server on the port specified
by the ``ARCTOS_PORT`` environment variable (default ``5006``).

Example:
    Run the development server::

        just public-assets
        ARCTOS_PORT=5006 python run_app.py

    Then point the Dioxus frontend at it::

        ARCTOS_API_BASE=http://127.0.0.1:5006 dx serve
"""

import os
from app import create_app

# Create the app instance
app = create_app()

if __name__ == "__main__":
    # Port from env (e.g. 5006). For Dioxus dev (dx serve on 8080), run Flask on http:
    #   ARCTOS_PORT=5006 python run_app.py
    # Then use ARCTOS_API_BASE=http://127.0.0.1:5006 when building the frontend.
    port = int(os.environ.get("ARCTOS_PORT", "5006"))
    app.run(host="127.0.0.1", port=port, debug=False)
