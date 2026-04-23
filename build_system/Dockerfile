FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN uv pip install --system --no-cache \
    sphinx==8.1.3 \
    myst-parser==4.0.1 \
    furo==2025.12.19 \
    sphinx-autodoc-typehints==3.0.1

COPY pyproject.toml /tmp/pyproject.toml
RUN uv pip install --system --no-cache -r /tmp/pyproject.toml

WORKDIR /project
