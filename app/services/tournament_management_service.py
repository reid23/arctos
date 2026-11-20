"""Organizer setup of a tournament: creation, deletion, organizers, fields, tags, and script variables.

Callers authorize the organizer first (except :meth:`TournamentManagementService.create_tournament`,
which checks league membership itself); the SPA's session routes in
:mod:`app.routes.tournaments.management` / :mod:`~app.routes.tournaments.matches_admin`
and the public API's ``manage`` routes share these methods.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, NotFoundError, UnauthorizedError, ValidationError
from app.models.validators import URL_SLUG_ALLOWED_HINT, is_valid_url_slug
from app.services._common import resolve_actor
from app.services.dual_write import get_match_ref_initials, get_match_referee_rows
from app.services.license_service import LicenseService
from app.services.permission_service import PermissionService
from app.utils.datetime_helpers import now_utc_naive
from app.utils.helpers import resolve_tag_to_team
from app.utils.match_chain import delete_matches_with_children
from app.utils.scheduling import recompute_all_match_times
from models import (
    TO,
    Field,
    HeadRef,
    League,
    Match,
    PenaltyType,
    Player,
    PlayerRegistration,
    RegistrableConfig,
    ScheduleItem,
    ScriptVariable,
    SideComp,
    SideCompEntryNumber,
    SideCompRegistration,
    SideCompResult,
    Tag,
    Team,
    TeamRegistration,
    Tournament,
    db,
)
from app.domain.enums import MatchStatus

USAGE_EXAMPLES_SHOWN = 5


def _usage_summary(used: list[str]) -> str:
    return ", ".join(used[:USAGE_EXAMPLES_SHOWN]) + (" (and possibly more)" if len(used) > USAGE_EXAMPLES_SHOWN else "")


def _tag_usage(tournament_url: str, tag_name: str) -> list[str]:
    """Return list of human-readable strings describing where tag is used, or empty if not used."""
    from app.utils.parser import extract_tag_references

    tag_ref = f"tag::{tag_name}"
    used = []
    for m in Match.query.filter_by(event=tournament_url).all():
        if m.team1_initial and m.team1_initial.strip() == tag_ref:
            used.append(f'Team 1 of match "{m.name}"')
        if m.team2_initial and m.team2_initial.strip() == tag_ref:
            used.append(f'Team 2 of match "{m.name}"')
        if any(initial == tag_ref for initial in get_match_ref_initials(m)):
            used.append(f'Refs of match "{m.name}"')
        if m.skip_condition and tag_name in extract_tag_references(m.skip_condition):
            used.append(f'Skip condition of match "{m.name}"')
    for t in Tag.query.filter_by(event=tournament_url).all():
        if t.name == tag_name:
            continue
        expr = (t.expression or "").strip()
        if expr and tag_name in extract_tag_references(expr):
            used.append(f'Expression of tag "{t.name}"')
    for v in ScriptVariable.query.filter_by(event=tournament_url).all():
        expr = (v.expression or "").strip()
        if expr and tag_name in extract_tag_references(expr):
            used.append(f'Expression of variable "{v.name}"')
    return used


def _script_variable_usage(tournament_url: str, var_name: str, exclude_id=None) -> list[str]:
    """Where a script variable name is referenced (other vars, tags, skip conditions)."""
    from app.utils.parser import extract_variable_references

    used = []
    for v in ScriptVariable.query.filter_by(event=tournament_url).all():
        if exclude_id is not None and v.id == exclude_id:
            continue
        if var_name in extract_variable_references(v.expression or ""):
            used.append(f'Variable "{v.name}"')
    for t in Tag.query.filter_by(event=tournament_url).all():
        if var_name in extract_variable_references(t.expression or ""):
            used.append(f'Expression of tag "{t.name}"')
    for m in Match.query.filter_by(event=tournament_url).all():
        if var_name in extract_variable_references(m.skip_condition or ""):
            used.append(f'Skip condition of match "{m.name}"')
    return used


def validate_tag_expression(tournament_url, expression):
    """Validate an ASS tag expression: parse + type check (must include TEAM).

    Returns ``(normalized_expression_or_None, error_or_None)``. An empty or
    None expression normalizes to None (clears the tag's expression).
    """
    from app.utils.parser import DSLValidationError, _human_type_name, _infer_types, get_parser

    expression = (expression or "").strip()
    if not expression:
        return None, None
    try:
        parser = get_parser(tournament_url)
        warnings = parser.static_check(expression)
        if warnings:
            return None, "; ".join(warnings)
        result = parser.parse(expression)
    except DSLValidationError as e:
        return None, str(e)
    except Exception as e:
        return None, f"Parse error: {e}"
    types = _infer_types(result)
    if "TEAM" not in types:
        return None, f"Tag expression must resolve to a TEAM, got {_human_type_name(types)}."
    return expression, None


def _validate_script_variable(tournament_url, name, expression, exclude_id=None):
    """Validate a script variable's name and expression. Returns an error string or None."""
    from app.utils.parser import (
        RESERVED_IDENTIFIERS,
        DSLValidationError,
        extract_variable_references,
        get_parser,
        is_valid_identifier,
    )

    if not name:
        return "Name required"
    if not is_valid_identifier(name):
        return f"'{name}' is not a valid identifier."
    if name in RESERVED_IDENTIFIERS:
        return f"'{name}' is a builtin function or reserved word."
    query = ScriptVariable.query.filter_by(event=tournament_url, name=name)
    if exclude_id is not None:
        query = query.filter(ScriptVariable.id != exclude_id)
    if query.first():
        return f"Variable '{name}' already exists."
    if not expression:
        return "Expression required"

    # Static cycle check over variable-to-variable references, with this
    # variable's (new) expression substituted in.
    graph = {name: extract_variable_references(expression)}
    for row in ScriptVariable.query.filter_by(event=tournament_url).all():
        if exclude_id is not None and row.id == exclude_id:
            continue
        if row.name == name:
            continue
        graph[row.name] = extract_variable_references(row.expression)
    # Iterative DFS with colors, only following edges to defined variables.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}

    def _has_cycle_from(start):
        stack = [(start, iter(sorted(graph[start] & set(graph))))]
        color[start] = GRAY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if color[nxt] == GRAY:
                    return True
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, iter(sorted(graph[nxt] & set(graph)))))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
        return False

    if _has_cycle_from(name):
        return f"Cyclic variable reference involving '{name}'."

    # The expression must parse and evaluate cleanly (other variables are
    # available in the environment; the candidate itself is not).
    try:
        parser = get_parser(tournament_url)
        warnings = parser.static_check(expression)
        if warnings:
            return "; ".join(warnings)
        parser.parse(expression)
    except DSLValidationError as e:
        return str(e)
    except Exception as e:
        return f"Parse error: {e}"
    return None


@dataclass(frozen=True)
class TournamentManagementService:
    """Organizer tournament setup. All methods are static; the class acts as a typed namespace."""

    @staticmethod
    def create_tournament(
        *, actor_user_id: str, actor_user_type: str, name: str, url: str, league_id: str
    ) -> Result[Tournament, ArctosError]:
        """Create a tournament with the actor as its organizer.

        A standalone tournament gets its own registration config; one attached
        to *league_id* (blank for none) shares the league's, and the actor must
        organize that league.
        """
        name = name.strip()
        url = url.strip()
        if not name or not url:
            return Err(ValidationError(f"Name and URL slug are required. {URL_SLUG_ALLOWED_HINT}"))
        if not is_valid_url_slug(url):
            return Err(ValidationError(URL_SLUG_ALLOWED_HINT))
        if Tournament.query.filter_by(url=url).first():
            return Err(ValidationError("Tournament URL already exists"))

        league_id = league_id.strip()
        if league_id:
            if not League.query.get(league_id):
                return Err(ValidationError("League not found"))
            if not PermissionService.is_league_organizer(league_id, resolve_actor(actor_user_id, actor_user_type)):
                return Err(UnauthorizedError("You must be an organizer of that league to attach a tournament to it."))

        start_date = now_utc_naive()
        tournament = Tournament(
            url=url,
            name=name,
            start_date=start_date,
            end_date=start_date,
            league_id=league_id or None,
        )
        if not league_id:
            config = RegistrableConfig(team_reg_fee=0.0, player_reg_fee=0.0)
            db.session.add(config)
            db.session.flush()
            tournament.registrable_config_id = config.id

        db.session.add(tournament)
        db.session.flush()
        db.session.add(TO(user_id=actor_user_id, user_type=actor_user_type, event=url))
        db.session.commit()
        return Ok(tournament)

    @staticmethod
    def delete_tournament(tournament_url: str) -> Result[str, ArctosError]:
        """Permanently delete a tournament and everything in it; returns its name."""
        tournament = Tournament.query.filter_by(url=tournament_url).first()
        if tournament is None:
            return Err(NotFoundError("Tournament not found"))

        # Delete in order to respect foreign key constraints.
        # Order: side comp results & registrations -> side comps; points & match notes -> matches;
        # then penalty types, head refs, registrations, TOs, fields, tags; finally tournament.

        ScheduleItem.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        side_comp_ids = [sc.id for sc in SideComp.query.filter_by(event=tournament_url).all()]
        if side_comp_ids:
            SideCompResult.query.filter(SideCompResult.comp.in_(side_comp_ids)).delete(synchronize_session=False)
            SideCompRegistration.query.filter(SideCompRegistration.comp.in_(side_comp_ids)).delete(
                synchronize_session=False
            )

        SideComp.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        if not tournament.league_id:
            SideCompEntryNumber.query.filter_by(
                scope=SideCompEntryNumber.scope_for_tournament(tournament_url, None)
            ).delete(synchronize_session=False)

        from app.services.bracket_cleanup_service import cleanup_tournament_bracket_assets

        # Remove uploaded canvas/legacy bracket images from disk before rows go away.
        cleanup_tournament_bracket_assets(tournament_url)

        match_uuids = [m.uuid for m in Match.query.filter_by(event=tournament_url).all()]
        delete_matches_with_children(match_uuids)
        # PenaltyType after MatchNote (notes reference penalty_type_id)
        # Only delete event-level penalty types; league events use league's penalty types
        if not tournament.league_id:
            PenaltyType.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        HeadRef.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        PlayerRegistration.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        TeamRegistration.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        Field.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        Tag.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        ScriptVariable.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        TO.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        # Bracket canvas rows (placements already removed with matches).
        from models import BracketImage, BracketLabeledTeam, BracketText

        BracketText.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        BracketLabeledTeam.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        BracketImage.query.filter_by(event=tournament_url).delete(synchronize_session=False)
        # Historical tournaments: unclaimed placeholder teams go with the tournament.
        from models import TeamClaimInvitation

        placeholder_ids = [t.id for t in Team.query.filter_by(placeholder_event=tournament_url).all()]
        if placeholder_ids:
            TeamClaimInvitation.query.filter(TeamClaimInvitation.placeholder_team.in_(placeholder_ids)).delete(
                synchronize_session=False
            )
            Team.query.filter(Team.id.in_(placeholder_ids)).delete(synchronize_session=False)
        config_id = tournament.registrable_config_id if not tournament.league_id else None
        LicenseService.release_event(tournament_url)
        name = tournament.name
        db.session.delete(tournament)
        if config_id:
            config = RegistrableConfig.query.get(config_id)
            if config:
                db.session.delete(config)
        db.session.commit()
        return Ok(name)

    @staticmethod
    def add_organizer(tournament_url: str, user_id: str, user_type: str) -> Result[str, ArctosError]:
        """Make a player or team an organizer of a standalone tournament; returns their name."""
        tournament = Tournament.query.filter_by(url=tournament_url).first()
        if tournament is None:
            return Err(NotFoundError("Tournament not found"))
        if tournament.league_id:
            return Err(UnauthorizedError("TOs for league events are managed from the league page."))

        user_id = user_id.strip()
        user_type = user_type.strip().lower()
        if not user_id or user_type not in ["player", "team"]:
            return Err(ValidationError("Invalid user ID or type"))

        if user_type == "player":
            user = Player.query.get(user_id)
            if not user:
                return Err(NotFoundError(f'Player with ID "{user_id}" not found'))
        else:
            user = Team.query.get(user_id)
            if not user:
                return Err(NotFoundError(f'Team with ID "{user_id}" not found'))

        if TO.query.filter_by(user_id=user_id, user_type=user_type, event=tournament_url).first():
            return Err(ValidationError("This user is already a TO for this tournament"))

        db.session.add(TO(user_id=user_id, user_type=user_type, event=tournament_url))
        db.session.commit()
        return Ok(user.name)

    @staticmethod
    def remove_organizer(
        tournament_url: str, organizer_id, *, actor_user_id: str, actor_user_type: str
    ) -> Result[str, ArctosError]:
        """Remove TO entry *organizer_id* from a standalone tournament; returns the removed user's name.

        Actors can't remove themselves or a team they manage.
        """
        tournament = Tournament.query.filter_by(url=tournament_url).first()
        if tournament is None:
            return Err(NotFoundError("Tournament not found"))
        if tournament.league_id:
            return Err(UnauthorizedError("TOs for league events are managed from the league page."))

        organizer = TO.query.get(organizer_id)
        if organizer is None:
            return Err(NotFoundError("Organizer not found"))
        if organizer.event != tournament_url:
            return Err(ValidationError("Invalid TO entry"))

        actor = resolve_actor(actor_user_id, actor_user_type)
        if (organizer.user_id == actor_user_id and organizer.user_type == actor_user_type) or (
            organizer.user_type == "team" and PermissionService.can_manage_team(organizer.user_id, actor)
        ):
            return Err(ValidationError("You cannot remove yourself as a TO"))

        user = resolve_actor(organizer.user_id, organizer.user_type)
        name = user.name if user else organizer.user_id
        db.session.delete(organizer)
        db.session.commit()
        return Ok(name)

    @staticmethod
    def create_field(tournament_url: str, name: str) -> Result[Field, ArctosError]:
        name = name.strip()
        if not name:
            return Err(ValidationError("Name required"))
        if Field.query.filter_by(event=tournament_url, name=name).first():
            return Err(ValidationError("Field already exists"))
        field = Field(event=tournament_url, name=name)
        db.session.add(field)
        db.session.commit()
        return Ok(field)

    @staticmethod
    def rename_field(tournament_url: str, field_id: int, name: str) -> Result[Field, ArctosError]:
        """Rename a field, moving its matches and schedule items to the new name."""
        field = Field.query.filter_by(id=field_id, event=tournament_url).first()
        if field is None:
            return Err(NotFoundError("Field not found"))
        new_field_name = name.strip()
        if not new_field_name:
            return Err(ValidationError("Field name required"))

        old_field_name = field.name
        field.name = new_field_name
        if old_field_name != new_field_name:
            ScheduleItem.query.filter_by(event=tournament_url, field=old_field_name).update({"field": new_field_name})
            for match in Match.query.filter_by(event=tournament_url, field=old_field_name).all():
                match.field = new_field_name
        db.session.commit()
        return Ok(field)

    @staticmethod
    def delete_field(tournament_url: str, field_id: int) -> Result[None, ArctosError]:
        """Delete a field without matches; schedule items on it lose their field."""
        field = Field.query.filter_by(id=field_id, event=tournament_url).first()
        if field is None:
            return Err(NotFoundError("Field not found"))
        if Match.query.filter_by(event=tournament_url, field=field.name).first():
            return Err(ValidationError("Cannot delete field with matches"))
        ScheduleItem.query.filter_by(event=tournament_url, field=field.name).update({"field": None})
        db.session.delete(field)
        db.session.commit()
        return Ok(None)

    @staticmethod
    def create_tag(tournament_url: str, name: str, expression) -> Result[Tag, ArctosError]:
        """Create a tag, optionally computed by an ASS *expression* that resolves to a team."""
        name = name.strip()
        if not name:
            return Err(ValidationError("Name required"))
        if "::" in name:
            return Err(ValidationError('Tag name cannot contain "::"'))
        if Tag.query.filter_by(event=tournament_url, name=name).first():
            return Err(ValidationError("Tag already exists"))
        expression, error = validate_tag_expression(tournament_url, expression)
        if error:
            return Err(ValidationError(error))
        tag = Tag(event=tournament_url, name=name, expression=expression)
        db.session.add(tag)
        db.session.commit()
        return Ok(tag)

    @staticmethod
    def update_tag(tournament_url: str, tag_id: int, data: dict) -> Result[Tag, ArctosError]:
        """Rename a tag (``name``, required) and replace its ``expression`` when given.

        A tag that matches, tags, or variables refer to can't be renamed.
        """
        tag = Tag.query.filter_by(id=tag_id, event=tournament_url).first()
        if tag is None:
            return Err(NotFoundError("Tag not found"))
        new_name = (data.get("name") or "").strip()
        if not new_name:
            return Err(ValidationError("Name required"))
        if "::" in new_name:
            return Err(ValidationError('Tag name cannot contain "::"'))
        if new_name != tag.name:
            used = _tag_usage(tournament_url, tag.name)
            if used:
                return Err(ValidationError(f'Cannot rename tag "{tag.name}": it is used in ' + _usage_summary(used)))
            if Tag.query.filter_by(event=tournament_url, name=new_name).first():
                return Err(ValidationError("Tag already exists"))
        if "expression" in data:
            expression, error = validate_tag_expression(tournament_url, data.get("expression"))
            if error:
                return Err(ValidationError(error))
            tag.expression = expression
        tag.name = new_name
        db.session.commit()
        recompute_all_match_times(tournament_url)
        return Ok(tag)

    @staticmethod
    def assign_tag(tournament_url: str, tag_id, data: dict) -> Result[Tag, ArctosError]:
        """Replace a tag's ``expression`` and/or manual ``team_id`` override when given.

        Unstarted matches whose teams or refs name the tag pick up the tag's
        team (the override if set, else the expression's result).
        """
        tag = Tag.query.filter_by(id=tag_id, event=tournament_url).first()
        if tag is None:
            return Err(NotFoundError("Tag not found"))
        if "expression" in data:
            expression, error = validate_tag_expression(tournament_url, data.get("expression"))
            if error:
                return Err(ValidationError(error))
            tag.expression = expression
        if "team_id" in data:
            team_id = data.get("team_id")
            tag.team = team_id if team_id else None
        db.session.commit()

        effective_team = resolve_tag_to_team(f"tag::{tag.name}", tournament_url)
        tag_ref = f"tag::{tag.name}"
        for match in Match.query.filter_by(event=tournament_url).all():
            if match.status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED, MatchStatus.IN_PROGRESS):
                continue
            if match.team1_initial == tag_ref:
                match.team1 = effective_team
            if match.team2_initial == tag_ref:
                match.team2 = effective_team
            for row in get_match_referee_rows(match):
                if (row.initial or "").strip() == tag_ref:
                    row.team_id = effective_team

        db.session.commit()
        recompute_all_match_times(tournament_url)
        return Ok(tag)

    @staticmethod
    def delete_tag(tournament_url: str, tag_id: int) -> Result[None, ArctosError]:
        """Delete a tag nothing in the schedule refers to."""
        tag = Tag.query.filter_by(id=tag_id, event=tournament_url).first()
        if tag is None:
            return Err(NotFoundError("Tag not found"))
        used = _tag_usage(tournament_url, tag.name)
        if used:
            return Err(ValidationError(f'Cannot delete tag "{tag.name}": it is used in ' + _usage_summary(used)))
        # Scrub any bracket/diagram soft refs (not covered by schedule usage check).
        from app.services.bracket_cleanup_service import scrub_deleted_tags

        scrub_deleted_tags(tournament_url, [tag.name])
        db.session.delete(tag)
        db.session.commit()
        return Ok(None)

    @staticmethod
    def create_script_variable(tournament_url: str, name: str, expression: str) -> Result[ScriptVariable, ArctosError]:
        name = name.strip()
        expression = expression.strip()
        if error := _validate_script_variable(tournament_url, name, expression):
            return Err(ValidationError(error))
        variable = ScriptVariable(event=tournament_url, name=name, expression=expression)
        db.session.add(variable)
        db.session.commit()
        recompute_all_match_times(tournament_url)
        return Ok(variable)

    @staticmethod
    def update_script_variable(
        tournament_url: str, variable_id: int, name: str | None, expression: str | None
    ) -> Result[ScriptVariable, ArctosError]:
        """Change a variable's name and/or expression; blank or missing keeps the current one.

        A variable that others refer to can't be renamed.
        """
        variable = ScriptVariable.query.filter_by(id=variable_id, event=tournament_url).first()
        if variable is None:
            return Err(NotFoundError("Script variable not found"))
        name = (name or variable.name).strip()
        expression = (expression or variable.expression).strip()
        if error := _validate_script_variable(tournament_url, name, expression, exclude_id=variable.id):
            return Err(ValidationError(error))

        if name != variable.name:
            used = _script_variable_usage(tournament_url, variable.name, exclude_id=variable.id)
            if used:
                return Err(
                    ValidationError(f'Cannot rename variable "{variable.name}": it is used in ' + _usage_summary(used))
                )

        variable.name = name
        variable.expression = expression
        db.session.commit()
        recompute_all_match_times(tournament_url)
        return Ok(variable)

    @staticmethod
    def delete_script_variable(tournament_url: str, variable_id: int) -> Result[None, ArctosError]:
        variable = ScriptVariable.query.filter_by(id=variable_id, event=tournament_url).first()
        if variable is None:
            return Err(NotFoundError("Script variable not found"))
        used = _script_variable_usage(tournament_url, variable.name, exclude_id=variable.id)
        if used:
            return Err(
                ValidationError(f'Cannot delete variable "{variable.name}": it is used in ' + _usage_summary(used))
            )
        db.session.delete(variable)
        db.session.commit()
        recompute_all_match_times(tournament_url)
        return Ok(None)
