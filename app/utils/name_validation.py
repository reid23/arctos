"""Validation for match names and team pseudonyms (reserved punctuation for parsing/exports)."""

from __future__ import annotations

from app.error_values import Null, Option, Some


def match_name_char_error(name: str) -> Option[str]:
    """Return an error message if *name* contains characters forbidden in match names.

    Match names must not contain ``","`` (used as a separator in CSV columns),
    ``"::"`` (reserved for winner/loser reference syntax), or ``"/"`` (path
    separator in break-group API routes).

    Args:
        name: The candidate match name.

    Returns:
        ``Some`` containing an error message for forbidden punctuation,
        or ``Null`` when the name passes this check. Callers validate
        whether a name is required separately.
    """
    if "," in name:
        return Some('Match names cannot contain ",".')
    if "::" in name:
        return Some('Match names cannot contain "::".')
    if "/" in name:
        return Some('Match names cannot contain "/".')
    return Null()


def team_pseudonym_char_error(pseudonym: str) -> Option[str]:
    """Return an error message if *pseudonym* contains forbidden characters.

    Team pseudonyms must not contain ``","`` or ``"::"`` for the same
    reasons as match names.

    Args:
        pseudonym: The candidate team pseudonym.

    Returns:
        ``Some`` containing an error message for forbidden punctuation,
        or ``Null`` when the pseudonym passes this check. Callers validate
        whether a pseudonym is required separately.
    """
    if "," in pseudonym:
        return Some('Team pseudonyms cannot contain ",".')
    if "::" in pseudonym:
        return Some('Team pseudonyms cannot contain "::".')
    return Null()
