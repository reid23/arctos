# `static/` - server-served static assets

Files Flask serves directly from `/static/...`.

The contents are mostly self-explanatory: a few mascot / UI icons, a
client-side Kalman filter under `js/`, and stone-elimination sound
effects under `stones/`.

The directory is also the parent of two app-managed subtrees that are
**not** in version control:

- `static/uploads/...` - user-uploaded profile photos, waivers, etc.
  Created at runtime, served back with cache headers (see
  `app/__init__.py::add_cache_headers`).
- `static/uploads/videos/...` - temporary footage upload artefacts.
  Excluded from the docs build (`docs/conf.py::exclude_patterns`).

## How files here get served

Flask is configured with `static_folder="../static"` in
[`app/__init__.py`](../app/__init__.py). Any file at `static/foo.png`
is reachable at `/static/foo.png`. Cache headers (`max-age=3600,
public`) are applied by the `add_cache_headers` after-request hook.

In production, nginx fronts Flask and may serve `/static/...`
directly (faster, no Python in the path). The directory layout is the
same either way.

## Adding a static asset

- One-off image referenced by an existing template / SPA page -> drop
  it here.
- Asset bundled with the SPA (CSS, JS, images used in Dioxus
  components) -> put it under `frontend/` and let `dx bundle` package it.

