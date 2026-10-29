"""Historical tournament records: creation, placeholder teams, and match entry.

A historical tournament (``Tournament.historical``) records an event that was
not run live in Arctos. Its organisers enter the teams and the finished
matches after the fact.

Teams are entered as placeholder :class:`~app.models.user.Team` rows (see
``Team.placeholder_event``) so matches can point at them through the usual
``teams.id`` foreign keys. :mod:`app.services.historical_claims_service`
merges a placeholder into a real team once that team claims it.

A match's team and referee slots accept a team id or a
``<match name>::winner`` / ``<match name>::loser`` reference. A reference is
kept in the slot's ``_initial`` column and resolved straight away into the
concrete column. Saving a match re-resolves every match that references it,
transitively, and renaming a match rewrites the references to it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.domain.enums import WinnerSide, parse_enum
from app.error_values import Err, Ok, Result, Some, allow_Q
from app.exceptions import ArctosError, NotFoundError, UnauthorizedError, ValidationError

if TYPE_CHECKING:  # pragma: no cover
    from models import Match, MatchReferee, TeamRegistration, Tournament

#: Placeholder team ids start with this; real usernames must start with a
#: letter or digit, so the two can never collide.
PLACEHOLDER_ID_PREFIX = "_"

_REFERENCE_QUALIFIERS = ("winner", "loser")


def _parse_reference(token: str | None) -> tuple[str, str] | None:
    """Split ``"<match name>::winner"`` or ``"::loser"`` into ``(match name, qualifier)``."""
    base, separator, qualifier = (token or "").strip().rpartition("::")
    qualifier = qualifier.strip().lower()
    if not separator or qualifier not in _REFERENCE_QUALIFIERS or not base.strip():
        return None
    return base.strip(), qualifier


def _parse_date(value: str | None, label: str) -> Result[datetime, ArctosError]:
    try:
        return Ok(datetime.strptime((value or "").strip(), "%Y-%m-%d"))
    except ValueError:
        return Err(ValidationError(f"{label} must be a date"))


def _normalize_shortname(raw) -> Result[str | None, ArctosError]:
    """Blank becomes ``None``. A value must fit the short-name column and use allowed characters."""
    from app.models.constants import SHORTNAME_LEN
    from app.utils.name_validation import team_pseudonym_char_error

    if raw is None:
        return Ok(None)
    shortname = str(raw).strip()
    if not shortname:
        return Ok(None)
    if len(shortname) > SHORTNAME_LEN:
        return Err(ValidationError(f"Short name must be at most {SHORTNAME_LEN} characters"))
    match team_pseudonym_char_error(shortname):
        case Some(problem):
            return Err(ValidationError(f"{shortname}: {problem}"))
    return Ok(shortname)


def _clean_team_entries(raw_entries: list | None) -> Result[list[tuple[str, str | None]], ArctosError]:
    """Normalize team entries to ``(name, shortname)``.

    Each entry is a name string, or a mapping with ``name`` and optional
    ``shortname``. Blank names are dropped. Repeated names keep the first.
    Short names, when set, must be unique in this list.
    """
    from app.utils.name_validation import team_pseudonym_char_error

    entries: list[tuple[str, str | None]] = []
    seen_names: set[str] = set()
    seen_shortnames: set[str] = set()
    for raw in raw_entries or []:
        if isinstance(raw, str):
            name, raw_shortname = raw, None
        elif isinstance(raw, dict):
            name, raw_shortname = raw.get("name") or "", raw.get("shortname")
        else:
            continue
        name = str(name).strip()
        if not name or name.casefold() in seen_names:
            continue
        match team_pseudonym_char_error(name):
            case Some(problem):
                return Err(ValidationError(f"{name}: {problem}"))
        shortname = _normalize_shortname(raw_shortname).Q()
        if shortname is not None and shortname.casefold() in seen_shortnames:
            return Err(ValidationError(f"Short name already used: {shortname}"))
        seen_names.add(name.casefold())
        if shortname is not None:
            seen_shortnames.add(shortname.casefold())
        entries.append((name, shortname))
    return Ok(entries)


def _referees_by_match(match_uuids: list[str]) -> dict[str, list["MatchReferee"]]:
    from models import MatchReferee

    rows_by_match: dict[str, list] = {}
    if not match_uuids:
        return rows_by_match
    for row in MatchReferee.query.filter(MatchReferee.match_uuid.in_(match_uuids)).order_by(MatchReferee.slot).all():
        rows_by_match.setdefault(row.match_uuid, []).append(row)
    return rows_by_match


@dataclass
class _MatchSlots:
    """In-memory copy of a match's team and referee slots, for planning reference updates."""

    match: "Match | None"
    name: str
    team1: str | None
    team2: str | None
    team1_initial: str | None
    team2_initial: str | None
    winner: WinnerSide | None
    referee_team_ids: list[str] = field(default_factory=list)
    referee_initials: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, match: "Match", referee_rows: list["MatchReferee"]) -> "_MatchSlots":
        winner = parse_enum(WinnerSide, match.match_winner)
        return cls(
            match=match,
            name=match.name,
            team1=match.team1,
            team2=match.team2,
            team1_initial=match.team1_initial,
            team2_initial=match.team2_initial,
            winner=winner.val if isinstance(winner, Some) else None,
            referee_team_ids=[row.team_id or "" for row in referee_rows],
            referee_initials=[row.initial or "" for row in referee_rows],
        )

    def team_for(self, qualifier: str) -> str | None:
        if self.winner is None:
            return None
        winner_is_team1 = self.winner == WinnerSide.TEAM1
        if qualifier == "winner":
            return self.team1 if winner_is_team1 else self.team2
        return self.team2 if winner_is_team1 else self.team1

    def referenced_match_names(self) -> set[str]:
        tokens = [self.team1_initial, self.team2_initial, *self.referee_initials]
        return {parsed[0] for parsed in map(_parse_reference, tokens) if parsed}

    def conflict(self) -> str | None:
        """Describe what makes these slots inconsistent, or return ``None``."""
        if self.team1 and self.team1 == self.team2:
            return "the same team on both sides"
        if any(team_id and team_id in (self.team1, self.team2) for team_id in self.referee_team_ids):
            return "a playing team refereeing"
        return None


@dataclass(frozen=True)
class HistoricalRecordsService:
    """Organiser-side data entry for historical tournaments."""

    @staticmethod
    @allow_Q
    def organizer_tournament(
        tournament_url: str, actor_user_id: str, actor_user_type: str
    ) -> Result["Tournament", ArctosError]:
        """Load *tournament_url*, requiring it to be historical and the actor to organise it."""
        from app.services._common import get_tournament_or_err, resolve_actor
        from app.services.permission_service import PermissionService

        tournament = get_tournament_or_err(tournament_url).Q()
        if not tournament.historical:
            return Err(ValidationError("This is not a historical tournament"))
        actor = resolve_actor(actor_user_id, actor_user_type)
        if not PermissionService.is_tournament_organizer_of(tournament, actor):
            return Err(UnauthorizedError("Only tournament organizers can edit these records"))
        return Ok(tournament)

    @staticmethod
    @allow_Q
    def create_tournament(
        *,
        actor_user_id: str,
        actor_user_type: str,
        name: str,
        url: str,
        location: str | None,
        start_date: str,
        end_date: str | None,
        team_names: list | None,
    ) -> Result["Tournament", ArctosError]:
        """Create an unpublished historical tournament with the actor as its organiser.

        Args:
            actor_user_id: The creating user, who becomes the tournament's TO.
            actor_user_type: ``"player"`` or ``"team"``.
            name: Display name.
            url: URL slug; must be unique.
            location: Where the tournament took place.
            start_date: ``YYYY-MM-DD``; may not be in the future.
            end_date: ``YYYY-MM-DD``, or blank for a one-day tournament.
            team_names: Teams that attended, each a name or a mapping with
                ``name`` and optional ``shortname``. Each becomes a placeholder.

        Returns:
            ``Ok(tournament)`` or a validation ``Err``.
        """
        from app.models.validators import URL_SLUG_ALLOWED_HINT, is_valid_url_slug
        from app.utils.datetime_helpers import now_utc_naive
        from models import TO, RegistrableConfig, Tournament, db

        name = (name or "").strip()
        url = (url or "").strip()
        if not name or not url:
            return Err(ValidationError("Name and URL slug are required"))
        if not is_valid_url_slug(url):
            return Err(ValidationError(URL_SLUG_ALLOWED_HINT))
        if Tournament.query.get(url) is not None:
            return Err(ValidationError("Tournament URL already exists"))
        start = _parse_date(start_date, "Start date").Q()
        end = _parse_date(end_date, "End date").Q() if (end_date or "").strip() else start
        if end < start:
            return Err(ValidationError("End date can't be before the start date"))
        if start > now_utc_naive():
            return Err(ValidationError("A historical tournament can't start in the future"))
        teams = _clean_team_entries(team_names).Q()

        config = RegistrableConfig(team_reg_fee=0, player_reg_fee=0)
        db.session.add(config)
        db.session.flush()
        tournament = Tournament(
            url=url,
            name=name,
            location=(location or "").strip() or None,
            start_date=start,
            end_date=end,
            historical=True,
            schedule_published=True,
            registrable_config_id=config.id,
        )
        db.session.add(tournament)
        db.session.flush()
        db.session.add(TO(user_id=actor_user_id, user_type=actor_user_type, event=url))
        HistoricalRecordsService._add_placeholders(tournament, teams)
        db.session.commit()
        return Ok(tournament)

    @staticmethod
    @allow_Q
    def update_details(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        name: str,
        location: str | None,
        start_date: str,
        end_date: str | None,
        about: str | None,
        published: bool,
    ) -> Result["Tournament", ArctosError]:
        """Edit a historical tournament's name, place, dates, description, and visibility."""
        from models import db

        tournament = HistoricalRecordsService.organizer_tournament(tournament_url, actor_user_id, actor_user_type).Q()
        name = (name or "").strip()
        if not name:
            return Err(ValidationError("Name is required"))
        start = _parse_date(start_date, "Start date").Q()
        end = _parse_date(end_date, "End date").Q() if (end_date or "").strip() else start
        if end < start:
            return Err(ValidationError("End date can't be before the start date"))

        tournament.name = name
        tournament.location = (location or "").strip() or None
        tournament.start_date = start
        tournament.end_date = end
        tournament.about = about or None
        tournament.published = bool(published)
        db.session.commit()
        return Ok(tournament)

    @staticmethod
    def _add_placeholders(tournament: "Tournament", teams: list[tuple[str, str | None]]) -> list["TeamRegistration"]:
        """Add (without committing) a placeholder team and its confirmed registration per name."""
        from app.domain.enums import TeamRegistrationStatus
        from models import Team, TeamRegistration, db

        rows = [
            Team(id=f"{PLACEHOLDER_ID_PREFIX}{uuid.uuid4().hex[:12]}", name=name, placeholder_event=tournament.url)
            for name, _shortname in teams
        ]
        db.session.add_all(rows)
        # No relationship() links the two tables, so the ORM won't order these inserts by foreign key.
        db.session.flush()
        registrations = [
            TeamRegistration(
                event=tournament.url,
                team=team.id,
                pseudonym=team.name,
                shortname=shortname,
                status=TeamRegistrationStatus.CONFIRMED,
                paid=True,
                amount_paid=0,
            )
            for team, (_name, shortname) in zip(rows, teams, strict=True)
        ]
        db.session.add_all(registrations)
        db.session.flush()
        return registrations

    @staticmethod
    @allow_Q
    def add_teams(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        names: list | None,
    ) -> Result[list["TeamRegistration"], ArctosError]:
        """Add placeholder teams, rejecting names and short names already used."""
        from app.services.registration_resolver import team_registrations_for_tournament
        from models import db

        tournament = HistoricalRecordsService.organizer_tournament(tournament_url, actor_user_id, actor_user_type).Q()
        cleaned = _clean_team_entries(names).Q()
        if not cleaned:
            return Err(ValidationError("Enter at least one team name"))
        existing = team_registrations_for_tournament(tournament)
        taken_names = {registration.pseudonym.casefold() for registration in existing}
        taken_shortnames = {registration.shortname.casefold() for registration in existing if registration.shortname}
        duplicates = [name for name, _shortname in cleaned if name.casefold() in taken_names]
        if duplicates:
            return Err(ValidationError(f"Already in this tournament: {', '.join(duplicates)}"))
        duplicate_shortnames = [
            shortname for _name, shortname in cleaned if shortname and shortname.casefold() in taken_shortnames
        ]
        if duplicate_shortnames:
            return Err(ValidationError(f"Short name already used: {', '.join(duplicate_shortnames)}"))
        registrations = HistoricalRecordsService._add_placeholders(tournament, cleaned)
        db.session.commit()
        return Ok(registrations)

    @staticmethod
    @allow_Q
    def rename_team(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        team_id: str,
        name: str,
        shortname: str | None = None,
        update_shortname: bool = False,
    ) -> Result["TeamRegistration", ArctosError]:
        """Change the name, and optionally the short name, a team is listed under."""
        from app.services.registration_resolver import team_registrations_for_tournament
        from app.utils.name_validation import team_pseudonym_char_error
        from models import Team, db

        tournament = HistoricalRecordsService.organizer_tournament(tournament_url, actor_user_id, actor_user_type).Q()
        name = (name or "").strip()
        if not name:
            return Err(ValidationError("Team name is required"))
        match team_pseudonym_char_error(name):
            case Some(problem):
                return Err(ValidationError(problem))
        registrations = team_registrations_for_tournament(tournament)
        registration = next((r for r in registrations if r.team == team_id), None)
        if registration is None:
            return Err(NotFoundError("Team not found in this tournament"))
        if any(r.team != team_id and r.pseudonym.casefold() == name.casefold() for r in registrations):
            return Err(ValidationError(f"Already in this tournament: {name}"))

        if update_shortname:
            cleaned_shortname = _normalize_shortname(shortname).Q()
            if cleaned_shortname and any(
                other.team != team_id and other.shortname and other.shortname.casefold() == cleaned_shortname.casefold()
                for other in registrations
            ):
                return Err(ValidationError(f"Short name already used: {cleaned_shortname}"))
            registration.shortname = cleaned_shortname

        registration.pseudonym = name
        team = Team.query.get(team_id)
        if team is not None and team.placeholder_event == tournament.url:
            team.name = name
        db.session.commit()
        return Ok(registration)

    @staticmethod
    @allow_Q
    def remove_team(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        team_id: str,
    ) -> Result[None, ArctosError]:
        """Remove a team that plays or referees no match, along with its roster and claim invitation."""
        from app.services.registration_resolver import team_registration_for_tournament
        from models import Match, MatchReferee, PlayerRegistration, Team, TeamClaimInvitation, db

        tournament = HistoricalRecordsService.organizer_tournament(tournament_url, actor_user_id, actor_user_type).Q()
        registration = team_registration_for_tournament(tournament, team_id)
        if registration is None:
            return Err(NotFoundError("Team not found in this tournament"))
        match_names = sorted(
            {
                m.name
                for m in Match.query.filter(Match.event == tournament.url)
                .filter((Match.team1 == team_id) | (Match.team2 == team_id))
                .all()
            }
            | {
                m.name
                for m in Match.query.join(MatchReferee, MatchReferee.match_uuid == Match.uuid)
                .filter(Match.event == tournament.url, MatchReferee.team_id == team_id)
                .all()
            }
        )
        if match_names:
            return Err(ValidationError(f"{registration.pseudonym} is still in: {', '.join(match_names)}"))

        PlayerRegistration.query.filter_by(event=tournament.url, team=team_id).delete(synchronize_session=False)
        db.session.delete(registration)
        team = Team.query.get(team_id)
        if team is not None and team.placeholder_event == tournament.url:
            TeamClaimInvitation.query.filter_by(placeholder_team=team_id).delete(synchronize_session=False)
            db.session.flush()
            db.session.delete(team)
        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def save_match(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        match_uuid: str | None,
        name: str,
        team1: str,
        team2: str,
        referees: list | None,
        winner: str,
        points: list | None,
        set_type: str | None = None,
    ) -> Result["Match", ArctosError]:
        """Create (``match_uuid=None``) or replace a finished match.

        Args:
            tournament_url: The historical tournament.
            actor_user_id: Acting organiser.
            actor_user_type: ``"player"`` or ``"team"``.
            match_uuid: The match to replace, or ``None`` to create one.
            name: Match name, unique in the tournament.
            team1: Team id or ``<match>::winner`` / ``<match>::loser``.
            team2: As *team1*.
            referees: Referee slots, each as *team1*. Blanks are ignored.
            winner: ``"TEAM1"`` or ``"TEAM2"``.
            points: Points in order, each ``{"set": <n >= 1>, "winner": "TEAM1" | "TEAM2"}``.
            set_type: ``"SETS"`` or ``"STONES"``. Blank means sets.

        Returns:
            ``Ok(match)``, or an ``Err`` naming the first problem. Nothing is
            written on error, including when the change would leave a match
            that references this one inconsistent.
        """
        from app.domain.enums import MatchStatus, ScheduleType, SetType
        from app.services.dual_write import set_match_referees
        from app.services.registration_resolver import team_registrations_for_tournament
        from app.utils.datetime_helpers import now_utc_naive
        from app.utils.name_validation import match_name_char_error
        from models import Match, db

        tournament = HistoricalRecordsService.organizer_tournament(tournament_url, actor_user_id, actor_user_type).Q()
        matches = Match.query.filter_by(event=tournament.url).all()
        match = None
        if match_uuid:
            match = next((m for m in matches if m.uuid == match_uuid), None)
            if match is None:
                return Err(NotFoundError("Match not found"))

        name = (name or "").strip()
        if not name:
            return Err(ValidationError("Match name is required"))
        match match_name_char_error(name):
            case Some(problem):
                return Err(ValidationError(problem))
        if any(m.name == name and m is not match for m in matches):
            return Err(ValidationError(f"There is already a match named {name}"))
        winner_side = parse_enum(WinnerSide, winner)
        if not isinstance(winner_side, Some):
            return Err(ValidationError("Choose who won the match"))
        parsed_points = HistoricalRecordsService._parse_points(points).Q()
        chosen_set_type = parse_enum(SetType, (set_type or "").strip() or SetType.SETS.value)
        if not isinstance(chosen_set_type, Some):
            return Err(ValidationError("Scoring is sets or stones"))

        referee_rows = _referees_by_match([m.uuid for m in matches])
        slots_by_name = {m.name: _MatchSlots.of(m, referee_rows.get(m.uuid, [])) for m in matches if m is not match}
        registrations = team_registrations_for_tournament(tournament)
        team_ids = {r.team for r in registrations}
        team_id_by_name = {r.pseudonym.casefold(): r.team for r in registrations}

        def resolve(token: str | None, label: str) -> Result[tuple[str, str], ArctosError]:
            token = (token or "").strip()
            if not token:
                return Err(ValidationError(f"{label}: choose a team"))
            reference = _parse_reference(token)
            if reference is not None:
                referenced_name, qualifier = reference
                referenced = slots_by_name.get(referenced_name)
                if referenced is None:
                    return Err(ValidationError(f"{label}: there is no other match named {referenced_name}"))
                team_id = referenced.team_for(qualifier)
                if not team_id:
                    return Err(ValidationError(f"{label}: {referenced_name} has no {qualifier}"))
                return Ok((team_id, f"{referenced_name}::{qualifier}"))
            team_id = token if token in team_ids else team_id_by_name.get(token.casefold())
            if team_id is None:
                return Err(ValidationError(f"{label}: {token} isn't one of this tournament's teams"))
            return Ok((team_id, team_id))

        team1_id, team1_initial = resolve(team1, "Team 1").Q()
        team2_id, team2_initial = resolve(team2, "Team 2").Q()
        referee_team_ids: list[str] = []
        referee_initials: list[str] = []
        for position, token in enumerate(referees or [], start=1):
            if not str(token or "").strip():
                continue
            referee_id, referee_initial = resolve(str(token), f"Referee {position}").Q()
            if referee_id not in referee_team_ids:
                referee_team_ids.append(referee_id)
                referee_initials.append(referee_initial)

        saved = _MatchSlots(
            match=match,
            name=name,
            team1=team1_id,
            team2=team2_id,
            team1_initial=team1_initial,
            team2_initial=team2_initial,
            winner=winner_side.val,
            referee_team_ids=referee_team_ids,
            referee_initials=referee_initials,
        )
        if problem := saved.conflict():
            return Err(ValidationError(f"A match can't have {problem}"))

        renamed: set[str] = set()
        if match is not None and match.name != name:
            renamed = HistoricalRecordsService._rename_references(slots_by_name.values(), match.name, name)
        if HistoricalRecordsService._depends_on(slots_by_name, saved.referenced_match_names(), name):
            return Err(ValidationError(f"{name} can't depend on a match that depends on it"))
        repointed = HistoricalRecordsService._propagate(slots_by_name, saved).Q()
        changed = [slots_by_name[slots_name] for slots_name in renamed | {slots.name for slots in repointed}]

        if match is None:
            match = Match(
                uuid=str(uuid.uuid4()),
                event=tournament.url,
                schedule_type=ScheduleType.STATIC,
                set_type=chosen_set_type.val,
            )
            db.session.add(match)
        if match.confirmed_start_time is None:
            # Historical matches have no real times; these only order them in entry order.
            latest = max((m.completed_time for m in matches if m.completed_time), default=None)
            match.confirmed_start_time = latest + timedelta(seconds=1) if latest else tournament.start_date
            match.completed_time = match.confirmed_start_time
        match.set_type = chosen_set_type.val
        match.name = name
        match.team1, match.team1_initial = team1_id, team1_initial
        match.team2, match.team2_initial = team2_id, team2_initial
        match.match_winner = winner_side.val
        match.status = MatchStatus.COMPLETED
        match.nsets = max((set_number for set_number, _ in parsed_points), default=1)
        match.finalized_by = actor_user_id
        match.finalized_at = now_utc_naive()
        db.session.flush()
        set_match_referees(match, referee_team_ids, referee_initials)
        HistoricalRecordsService._replace_points(match, parsed_points)

        for slots in changed:
            dependent = slots.match
            dependent.team1, dependent.team1_initial = slots.team1, slots.team1_initial
            dependent.team2, dependent.team2_initial = slots.team2, slots.team2_initial
            set_match_referees(dependent, slots.referee_team_ids, slots.referee_initials)
        db.session.commit()
        return Ok(match)

    @staticmethod
    def _parse_points(raw_points: list | None) -> Result[list[tuple[int, WinnerSide]], ArctosError]:
        if raw_points is None:
            return Ok([])
        if not isinstance(raw_points, list):
            return Err(ValidationError("Points must be a list"))
        parsed: list[tuple[int, WinnerSide]] = []
        for position, raw_point in enumerate(raw_points, start=1):
            raw_point = raw_point if isinstance(raw_point, dict) else {}
            set_number = raw_point.get("set")
            if isinstance(set_number, bool) or not isinstance(set_number, int) or set_number < 1:
                return Err(ValidationError(f"Point {position}: the set must be a whole number of at least 1"))
            side = parse_enum(WinnerSide, raw_point.get("winner"))
            if not isinstance(side, Some):
                return Err(ValidationError(f"Point {position}: choose who won it"))
            parsed.append((set_number, side.val))
        return Ok(parsed)

    @staticmethod
    def _replace_points(match: "Match", points: list[tuple[int, WinnerSide]]) -> None:
        """Replace *match*'s points, ordered by set, stamped just after its synthetic start time."""
        from models import MatchNote, Point, db

        old_point_ids = [point.uuid for point in Point.query.filter_by(match=match.uuid).all()]
        if old_point_ids:
            MatchNote.query.filter(MatchNote.point_id.in_(old_point_ids)).update(
                {"point_id": None}, synchronize_session=False
            )
            Point.query.filter(Point.uuid.in_(old_point_ids)).delete(synchronize_session=False)
        ordered = sorted(points, key=lambda point: point[0])
        for position, (set_number, side) in enumerate(ordered):
            db.session.add(
                Point(
                    match=match.uuid,
                    winner=side.value,
                    set_number=set_number,
                    stamp=match.confirmed_start_time + timedelta(microseconds=position),
                )
            )

    @staticmethod
    def _rename_references(all_slots, old_name: str, new_name: str) -> set[str]:
        """Rewrite ``old_name::<qualifier>`` initials in *all_slots* to *new_name*.

        Returns:
            Names of the matches whose initials changed.
        """

        def renamed(token: str | None) -> str | None:
            reference = _parse_reference(token)
            if reference is None or reference[0] != old_name:
                return token
            return f"{new_name}::{reference[1]}"

        changed: set[str] = set()
        for slots in all_slots:
            if old_name not in slots.referenced_match_names():
                continue
            slots.team1_initial = renamed(slots.team1_initial)
            slots.team2_initial = renamed(slots.team2_initial)
            slots.referee_initials = [renamed(initial) or "" for initial in slots.referee_initials]
            changed.add(slots.name)
        return changed

    @staticmethod
    def _depends_on(slots_by_name: dict[str, _MatchSlots], start_names: set[str], target_name: str) -> bool:
        """Whether following references from *start_names* ever reaches *target_name*."""
        pending = list(start_names)
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target_name:
                return True
            if current in visited or current not in slots_by_name:
                continue
            visited.add(current)
            pending.extend(slots_by_name[current].referenced_match_names())
        return False

    @staticmethod
    def _propagate(slots_by_name: dict[str, _MatchSlots], saved: _MatchSlots) -> Result[list[_MatchSlots], ArctosError]:
        """Re-resolve references across the tournament now that *saved* has new teams or a new winner.

        Mutates the in-memory slots only. References form no cycles, so
        iterating to a fixed point terminates; the pass limit is a guard.

        Returns:
            The slots whose teams changed, or an ``Err`` naming the first
            match the change would leave inconsistent.
        """
        lookup = {**slots_by_name, saved.name: saved}
        changed: dict[str, _MatchSlots] = {}

        def resolved(token: str | None, current: str | None) -> str | None:
            reference = _parse_reference(token)
            if reference is None or reference[0] not in lookup:
                return current
            return lookup[reference[0]].team_for(reference[1]) or current

        for _ in range(len(lookup) + 1):
            any_changed = False
            for slots in slots_by_name.values():
                team1 = resolved(slots.team1_initial, slots.team1)
                team2 = resolved(slots.team2_initial, slots.team2)
                referee_team_ids = [
                    resolved(initial, team_id) or ""
                    for initial, team_id in zip(slots.referee_initials, slots.referee_team_ids)
                ]
                if (team1, team2, referee_team_ids) != (slots.team1, slots.team2, slots.referee_team_ids):
                    slots.team1, slots.team2, slots.referee_team_ids = team1, team2, referee_team_ids
                    changed[slots.name] = slots
                    any_changed = True
            if not any_changed:
                break

        for slots in changed.values():
            if problem := slots.conflict():
                return Err(ValidationError(f"That change would give {slots.name} {problem}"))
        return Ok(list(changed.values()))

    @staticmethod
    @allow_Q
    def deletable_match(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        match_uuid: str,
    ) -> Result["Match", ArctosError]:
        """Return the match if it can be deleted: no other match references it.

        The caller deletes it with ``delete_matches_with_children``.
        """
        from models import Match

        tournament = HistoricalRecordsService.organizer_tournament(tournament_url, actor_user_id, actor_user_type).Q()
        matches = Match.query.filter_by(event=tournament.url).all()
        match = next((m for m in matches if m.uuid == match_uuid), None)
        if match is None:
            return Err(NotFoundError("Match not found"))
        referee_rows = _referees_by_match([m.uuid for m in matches])
        dependents = sorted(
            m.name
            for m in matches
            if m is not match and match.name in _MatchSlots.of(m, referee_rows.get(m.uuid, [])).referenced_match_names()
        )
        if dependents:
            return Err(ValidationError(f"{match.name} is referenced by: {', '.join(dependents)}"))
        return Ok(match)

    @staticmethod
    def editor_state(tournament: "Tournament") -> dict:
        """Everything the records editor shows: teams, claim invitations, and matches with points."""
        from collections import Counter

        from app.domain.enums import RegistrationStatus, SetType
        from app.services.registration_resolver import (
            player_registrations_for_tournament,
            team_registrations_for_tournament,
        )
        from models import Match, Point, Team, TeamClaimInvitation, TeamClaimRequest

        registrations = sorted(team_registrations_for_tournament(tournament), key=lambda r: r.pseudonym.casefold())
        team_ids = [r.team for r in registrations]
        teams_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(team_ids)).all()} if team_ids else {}
        invitations_by_placeholder = (
            {
                invitation.placeholder_team: invitation
                for invitation in TeamClaimInvitation.query.filter(
                    TeamClaimInvitation.placeholder_team.in_(team_ids)
                ).all()
            }
            if team_ids
            else {}
        )
        invited_ids = [invitation.invited_team for invitation in invitations_by_placeholder.values()]
        invited_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(invited_ids)).all()} if invited_ids else {}
        claim_requests = (
            TeamClaimRequest.query.filter(TeamClaimRequest.placeholder_team.in_(team_ids)).all() if team_ids else []
        )
        requesting_ids = [claim_request.requesting_team for claim_request in claim_requests]
        requesting_by_id = (
            {t.id: t for t in Team.query.filter(Team.id.in_(requesting_ids)).all()} if requesting_ids else {}
        )
        requests_by_placeholder: dict[str, list] = {}
        for claim_request in claim_requests:
            requests_by_placeholder.setdefault(claim_request.placeholder_team, []).append(claim_request)
        roster_sizes = Counter(
            registration.team
            for registration in player_registrations_for_tournament(
                tournament, statuses=[RegistrationStatus.CONFIRMED, RegistrationStatus.INVITED]
            )
            if registration.team
        )
        roster_request_counts = Counter(
            registration.team
            for registration in player_registrations_for_tournament(tournament, statuses=[RegistrationStatus.REQUESTED])
            if registration.team
        )

        matches = Match.query.filter_by(event=tournament.url).order_by(Match.completed_time, Match.name).all()
        match_uuids = [m.uuid for m in matches]
        referee_rows = _referees_by_match(match_uuids)
        points_by_match: dict[str, list] = {}
        if match_uuids:
            for point in Point.query.filter(Point.match.in_(match_uuids)).order_by(Point.stamp, Point.set_number).all():
                if not point.rerolled:
                    points_by_match.setdefault(point.match, []).append(point)

        match_counts: Counter = Counter()
        for m in matches:
            for team_id in {m.team1, m.team2, *(row.team_id for row in referee_rows.get(m.uuid, []))}:
                if team_id:
                    match_counts[team_id] += 1

        def team_json(registration) -> dict:
            team = teams_by_id.get(registration.team)
            invitation = invitations_by_placeholder.get(registration.team)
            invited = invited_by_id.get(invitation.invited_team) if invitation else None
            return {
                "id": registration.team,
                "name": registration.pseudonym,
                "shortname": registration.shortname,
                "placeholder": bool(team and team.placeholder_event == tournament.url),
                "account_name": team.name if team and not team.placeholder_event else None,
                "profile_photo": team.profile_photo if team else None,
                "match_count": match_counts.get(registration.team, 0),
                "roster_size": roster_sizes.get(registration.team, 0),
                "roster_request_count": roster_request_counts.get(registration.team, 0),
                "claim_invitation": (
                    {
                        "team_id": invitation.invited_team,
                        "team_name": invited.name if invited else invitation.invited_team,
                    }
                    if invitation
                    else None
                ),
                "claim_requests": [
                    {
                        "id": claim_request.id,
                        "team_id": claim_request.requesting_team,
                        "team_name": (
                            requesting_by_id[claim_request.requesting_team].name
                            if claim_request.requesting_team in requesting_by_id
                            else claim_request.requesting_team
                        ),
                    }
                    for claim_request in requests_by_placeholder.get(registration.team, [])
                ],
            }

        def match_json(m) -> dict:
            winner = parse_enum(WinnerSide, m.match_winner)
            recorded_set_type = parse_enum(SetType, m.set_type)
            set_type = recorded_set_type.val.value if isinstance(recorded_set_type, Some) else SetType.SETS.value
            return {
                "uuid": m.uuid,
                "name": m.name,
                "team1": m.team1,
                "team2": m.team2,
                "team1_initial": m.team1_initial,
                "team2_initial": m.team2_initial,
                "referees": [{"team_id": row.team_id, "initial": row.initial} for row in referee_rows.get(m.uuid, [])],
                "winner": winner.val.value if isinstance(winner, Some) else None,
                "set_type": set_type,
                "points": [
                    {"set": point.set_number or 1, "winner": point.winner} for point in points_by_match.get(m.uuid, [])
                ],
            }

        return {
            "teams": [team_json(registration) for registration in registrations],
            "matches": [match_json(m) for m in matches],
        }
