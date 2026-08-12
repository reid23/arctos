"""Normalize match names: strip leading/trailing whitespace.

Establishes the invariant that a match ``name`` has no surrounding whitespace.
Trims existing ``matches.name`` values and rewrites every reference that points
at a match so its base name is stripped too: the ``X::winner`` / ``X::loser``
tokens in ``matches.team1_initial`` / ``matches.team2_initial`` /
``match_referees.initial``, and the ``{X}`` match atoms / ``[X::winner]`` team
literals inside ``matches.skip_condition``.

Without this, a stored name like ``'Game 15 '`` never matches a reference whose
base is stripped at read time (``'Game 15'``), producing spurious "missing
match" warnings and broken dependency edges.

Revision ID: 0010_normalize_match_names
Revises: 0009_match_scheduled_start_time
Create Date: 2026-06-11
"""

from __future__ import annotations

import re
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010_normalize_match_names"
down_revision: Union[str, Sequence[str], None] = "0009_match_scheduled_start_time"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BRACE_ATOM = re.compile(r"\{([^}]*)\}")
_TEAM_REF_LITERAL = re.compile(r"\[([^\]]*::(?:winner|loser))\]")


def _rewrite_ref_csv(value: Optional[str]) -> Optional[str]:
    """Strip the base name of each ``BASE::qualifier`` token in a CSV ref field."""
    if not value or "::" not in value:
        return value
    changed = False
    out = []
    for part in value.split(","):
        if "::" in part:
            base, sep, qualifier = part.partition("::")
            stripped = base.strip()
            if stripped != base:
                changed = True
            out.append(stripped + sep + qualifier)
        else:
            out.append(part)
    return ",".join(out) if changed else value


def _rewrite_skip_condition(value: Optional[str]) -> Optional[str]:
    """Strip match-name bases inside ``{...}`` atoms and ``[...::winner|loser]`` literals."""
    if not value:
        return value

    def fix_brace(match: re.Match) -> str:
        return "{" + match.group(1).strip() + "}"

    def fix_team_ref(match: re.Match) -> str:
        base, sep, qualifier = match.group(1).partition("::")
        return "[" + base.strip() + sep + qualifier + "]"

    new_value = _BRACE_ATOM.sub(fix_brace, value)
    new_value = _TEAM_REF_LITERAL.sub(fix_team_ref, new_value)
    return new_value


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("UPDATE matches SET name = TRIM(name) WHERE name <> TRIM(name)")

    match_rows = bind.execute(
        sa.text("SELECT uuid, team1_initial, team2_initial, skip_condition FROM matches")
    ).fetchall()
    for uuid_, team1_initial, team2_initial, skip_condition in match_rows:
        new_team1 = _rewrite_ref_csv(team1_initial)
        new_team2 = _rewrite_ref_csv(team2_initial)
        new_skip = _rewrite_skip_condition(skip_condition)
        if new_team1 == team1_initial and new_team2 == team2_initial and new_skip == skip_condition:
            continue
        bind.execute(
            sa.text(
                "UPDATE matches SET team1_initial = :t1, team2_initial = :t2, skip_condition = :sc WHERE uuid = :uuid"
            ),
            {"t1": new_team1, "t2": new_team2, "sc": new_skip, "uuid": uuid_},
        )

    referee_rows = bind.execute(sa.text("SELECT id, initial FROM match_referees")).fetchall()
    for referee_id, initial in referee_rows:
        new_initial = _rewrite_ref_csv(initial)
        if new_initial == initial:
            continue
        bind.execute(
            sa.text("UPDATE match_referees SET initial = :initial WHERE id = :id"),
            {"initial": new_initial, "id": referee_id},
        )


def downgrade() -> None:
    # No-op: the stripped whitespace cannot be reconstructed, and the trimmed
    # state is the intended invariant.
    pass
