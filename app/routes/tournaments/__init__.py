"""Tournament-organiser routes - blueprint package.

Hosts the ``tournaments`` blueprint at ``/_api``.

Routes are split by sub-topic across submodules in this package; all
submodules share the same Blueprint object defined in this file, so
URL endpoint names are stable across moves:

- :mod:`app.routes.tournaments.management` - TO-side tournament/league
  CRUD, fields, tags, TO membership.
- :mod:`app.routes.tournaments.licenses` - voluntary license keys for
  standalone events.
- :mod:`app.routes.tournaments.scheduling` - schedule editing, recompute,
  push-back, import/export, DSL validation.
- :mod:`app.routes.tournaments.footage` - the footage API: TO-authenticated
  video upload (YouTube link or chunked file) plus footage list/delete.
- :mod:`app.routes.tournaments.scoreboard_overlay` - rendered scoreboard
  overlay frames for compositing over recorded match footage.
"""

from flask import Blueprint
from flask_executor import Executor


# for finalizing recordings which calls ffmpeg
# only one worker bc ffmpeg does its own parallelism
# so we only ever want to run one at a time
executor = Executor()

bp = Blueprint("tournaments", __name__, url_prefix="/_api")


# Register submodule handlers by importing them.
# This MUST be at the bottom so that bp and executor are already defined
# when submodules import them via `from . import bp`.
from app.routes.tournaments import (  # noqa: E402, F401
    brackets,
    footage,
    licenses,
    management,
    matches_admin,
    read,
    scheduling,
    schedule_items,
    scoreboard_overlay,
)
