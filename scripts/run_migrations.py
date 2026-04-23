#!/usr/bin/env python3
"""Run application-managed schema migrations."""

from __future__ import annotations

from app import create_app
from app.db_migrations import run_bootstrap_migrations
from models import db


def main() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        run_bootstrap_migrations(db)
    print("Schema migrations complete.")


if __name__ == "__main__":
    main()
