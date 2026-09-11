"""Validation for match names and team pseudonyms (reserved punctuation for parsing/exports)."""

from __future__ import annotations

from typing import Optional


def match_name_char_error(name: str) -> Optional[str]:
    """Return an error message if *name* contains characters forbidden in match names.

    Match names must not contain ``","`` (used as a separator in CSV columns),
    ``"::"`` (reserved for winner/loser reference syntax), or ``"/"`` (path
    separator in break-group API routes).

    Args:
        name: The candidate match name.

    Returns:
        A human-readable error string if the name is invalid, or ``None``
        if the name passes validation.
    """
    if "," in name:
        return 'Match names cannot contain ",".'
    if "::" in name:
        return 'Match names cannot contain "::".'
    if "/" in name:
        return 'Match names cannot contain "/".'
    return None


def team_pseudonym_char_error(pseudonym: str) -> Optional[str]:
    """Return an error message if *pseudonym* contains forbidden characters.

    Team pseudonyms must not contain ``","`` or ``"::"`` for the same
    reasons as match names.

    Args:
        pseudonym: The candidate team pseudonym.

    Returns:
        A human-readable error string if the pseudonym is invalid, or
        ``None`` if it passes validation.
    """
    if "," in pseudonym:
        return 'Team pseudonyms cannot contain ",".'
    if "::" in pseudonym:
        return 'Team pseudonyms cannot contain "::".'
    return None
