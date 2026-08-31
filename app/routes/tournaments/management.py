"""TO-side tournament and league management routes.

Create / edit / delete tournaments and leagues, field CRUD, tag CRUD,
TO membership management. Part of the ``tournaments`` blueprint.
"""

from flask import (
    request,
    jsonify,
)
from flask_login import login_required, current_user

from datetime import datetime

from models import (
    Tournament,
    Match,
    Field,
    Tag,
    TeamRegistration,
    PlayerRegistration,
    Team,
    TO,
    League,
    db,
    RegistrableConfig,
    Player,
    HeadRef,
    SideComp,
    SideCompRegistration,
    SideCompResult,
    PenaltyType,
)
from app.services._common import current_user_type
from app.services.dual_write import set_head_ref_allowlist_from_csv
from app.models.validators import URL_SLUG_ALLOWED_HINT, is_valid_url_slug
from app.utils.decorators import require_tournament_organizer
from app.utils.datetime_helpers import now_utc_naive


from app.services.permission_service import PermissionService

from . import bp, delete_matches_with_children


@bp.route("/create-tournament", methods=["POST"])
@login_required
def create_tournament():
    """Create a new tournament and assign the creator as TO.

    ``POST /_api/create-tournament``

    Creates the tournament record and, for standalone tournaments, a
    :class:`~app.models.registrable_config.RegistrableConfig`.  When
    *league_id* is provided the tournament inherits the league's config and
    the caller must be a league TO.

    Form Data:
        name (str): Display name for the tournament.
        url (str): URL slug (must be unique).
        league_id (str | None): Optional league to attach to.

    Returns:
        JSON ``{"success": true, "url": "<slug>"}`` on success, or error
        with HTTP 400/403.
    """
    name = request.form["name"].strip()
    url = request.form["url"].strip()

    if not name or not url:
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"Name and URL slug are required. {URL_SLUG_ALLOWED_HINT}",
                }
            ),
            400,
        )
    if not is_valid_url_slug(url):
        return jsonify({"success": False, "error": URL_SLUG_ALLOWED_HINT}), 400

    if Tournament.query.filter_by(url=url).first():
        return (
            jsonify({"success": False, "error": "Tournament URL already exists"}),
            400,
        )

    league_id = None
    raw_league_id = request.form.get("league_id", "").strip()
    if raw_league_id:
        league = League.query.get(raw_league_id)
        if not league:
            return jsonify({"success": False, "error": "League not found"}), 400
        if not PermissionService.is_league_organizer(raw_league_id, current_user):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "You must be an organizer of that league to attach a tournament to it.",
                    }
                ),
                403,
            )
        league_id = raw_league_id

    start_date = now_utc_naive()
    tournament = Tournament(
        url=url,
        name=name,
        start_date=start_date,
        end_date=start_date,
        league_id=league_id,
    )
    if not league_id:
        rc = RegistrableConfig(
            team_reg_fee=0.0,
            player_reg_fee=0.0,
        )
        db.session.add(rc)
        db.session.flush()
        tournament.registrable_config_id = rc.id

    db.session.add(tournament)
    db.session.flush()

    to_entry = TO(
        user_id=current_user.id,
        user_type=current_user_type(),
        event=url,
    )
    db.session.add(to_entry)
    db.session.commit()

    return (
        jsonify(
            {
                "success": True,
                "message": f'Tournament "{name}" created successfully!',
                "url": url,
            }
        ),
        200,
    )


@bp.route("/create-league", methods=["POST"])
@login_required
def create_league():
    """Create a new league. TOs create a new league for each season."""
    league_name = request.form.get("league_name", "").strip()
    league_url = request.form.get("league_url", "").strip()

    if not league_name or not league_url:
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"League name and URL slug are required. {URL_SLUG_ALLOWED_HINT}",
                }
            ),
            400,
        )
    if not is_valid_url_slug(league_url):
        return jsonify({"success": False, "error": URL_SLUG_ALLOWED_HINT}), 400

    if League.query.filter_by(url=league_url).first():
        return jsonify({"success": False, "error": "League URL already exists"}), 400

    rc = RegistrableConfig(
        team_reg_fee=0.0,
        player_reg_fee=0.0,
    )
    db.session.add(rc)
    db.session.flush()
    league = League(
        url=league_url,
        name=league_name,
        registrable_config_id=rc.id,
    )
    db.session.add(league)
    db.session.flush()

    to_entry = TO(
        user_id=current_user.id,
        user_type=current_user_type(),
        event=None,
        league_id=league_url,
    )
    db.session.add(to_entry)
    db.session.commit()

    return (
        jsonify(
            {
                "success": True,
                "message": f'League "{league_name}" created successfully!',
                "league_url": league_url,
            }
        ),
        200,
    )


@bp.route("/<tournament_url>/update-settings", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def update_tournament_settings(tournament_url):
    """Update tournament settings."""
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()

    tournament.name = request.form["name"]
    tournament.location = request.form.get("location", "")
    tournament.about = request.form.get("about", "")
    set_head_ref_allowlist_from_csv(tournament, request.form.get("head_refs_allowed_list", ""))
    tournament.head_refs_allow_reffing_teams = "head_refs_allow_reffing_teams" in request.form
    tournament.head_refs_allow_anyone = "head_refs_allow_anyone" in request.form
    tournament.published = "published" in request.form
    tournament.schedule_published = "schedule_published" in request.form
    if not tournament.league_id and tournament.registrable_config:
        rc = tournament.registrable_config
        rc.team_reg_fee = float(request.form.get("team_reg_fee", 0))
        rc.player_reg_fee = float(request.form.get("player_reg_fee", 0))
        rc.terms_link = request.form.get("terms_link", "") or None
        rc.team_registration_open = "team_registration_open" in request.form
        rc.player_registration_open = "player_registration_open" in request.form
        n_max = request.form.get("n_max_teams", "").strip()
        rc.n_max_teams = int(n_max) if n_max else None
        roster = request.form.get("max_team_size_roster", "").strip()
        rc.max_team_size_roster = int(roster) if roster else None
        field = request.form.get("max_team_size_field", "").strip()
        rc.max_team_size_field = int(field) if field else None
        if "require_waiver_signature" not in request.form:
            rc.waiver_filepath = None
            rc.waiver_sha256 = None

    if request.form.get("start_date"):
        tournament.start_date = datetime.strptime(request.form["start_date"], "%Y-%m-%d")

    end_date_val = request.form.get("end_date", "").strip()
    if not end_date_val:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "End date is required.",
                }
            ),
            400,
        )
    tournament.end_date = datetime.strptime(end_date_val, "%Y-%m-%d")

    db.session.commit()
    return (
        jsonify({"success": True, "message": "Tournament settings updated successfully!"}),
        200,
    )


@bp.route("/<tournament_url>/delete", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def delete_tournament(tournament_url):
    """Delete a tournament and all related data."""
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()

    # Verify confirmation URL slug
    confirm_url = request.form.get("confirm_url", "").strip()
    if confirm_url != tournament_url:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Confirmation URL does not match. Tournament not deleted.",
                }
            ),
            400,
        )

    # Delete in order to respect foreign key constraints.
    # Order: side comp results & registrations -> side comps; points & match notes -> matches;
    # then penalty types, head refs, registrations, TOs, fields, tags; finally tournament.

    side_comps = SideComp.query.filter_by(event=tournament_url).all()
    side_comp_ids = [sc.id for sc in side_comps]
    if side_comp_ids:
        SideCompResult.query.filter(SideCompResult.comp.in_(side_comp_ids)).delete(synchronize_session=False)
        SideCompRegistration.query.filter(SideCompRegistration.comp.in_(side_comp_ids)).delete(
            synchronize_session=False
        )

    SideComp.query.filter_by(event=tournament_url).delete(synchronize_session=False)

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
    TO.query.filter_by(event=tournament_url).delete(synchronize_session=False)
    # Bracket canvas rows (placements already removed with matches).
    from models import BracketImage, BracketLabeledTeam, BracketText

    BracketText.query.filter_by(event=tournament_url).delete(synchronize_session=False)
    BracketLabeledTeam.query.filter_by(event=tournament_url).delete(synchronize_session=False)
    BracketImage.query.filter_by(event=tournament_url).delete(synchronize_session=False)
    rc_id = tournament.registrable_config_id if not tournament.league_id else None
    db.session.delete(tournament)
    if rc_id:
        rc = RegistrableConfig.query.get(rc_id)
        if rc:
            db.session.delete(rc)
    db.session.commit()

    return (
        jsonify(
            {
                "success": True,
                "message": f'Tournament "{tournament.name}" has been permanently deleted.',
            }
        ),
        200,
    )


@bp.route("/<tournament_url>/add-to", methods=["POST"])
@login_required
def add_to(tournament_url):
    """Add a TO to the tournament."""

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    if tournament.league_id:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "TOs for league events are managed from the league page.",
                }
            ),
            403,
        )

    if not PermissionService.is_tournament_organizer(tournament_url, current_user):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    user_id = request.form.get("user_id", "").strip()
    user_type = request.form.get("user_type", "").strip().lower()

    if not user_id or user_type not in ["player", "team"]:
        return jsonify({"success": False, "error": "Invalid user ID or type"}), 400

    # Verify the user exists
    if user_type == "player":
        user = Player.query.get(user_id)
        if not user:
            return (
                jsonify({"success": False, "error": f'Player with ID "{user_id}" not found'}),
                404,
            )
    else:  # team
        user = Team.query.get(user_id)
        if not user:
            return (
                jsonify({"success": False, "error": f'Team with ID "{user_id}" not found'}),
                404,
            )

    # Check if TO already exists
    existing_to = TO.query.filter_by(user_id=user_id, user_type=user_type, event=tournament_url).first()

    if existing_to:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "This user is already a TO for this tournament",
                }
            ),
            400,
        )

    # Create new TO entry
    new_to = TO(user_id=user_id, user_type=user_type, event=tournament_url)
    db.session.add(new_to)
    db.session.commit()

    user_name = user.name if user else user_id
    return (
        jsonify({"success": True, "message": f"Successfully added {user_name} as a TO"}),
        200,
    )


@bp.route("/<tournament_url>/remove-to", methods=["POST"])
@login_required
def remove_to(tournament_url):
    """Remove a TO from the tournament."""

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    if tournament.league_id:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "TOs for league events are managed from the league page.",
                }
            ),
            403,
        )

    if not PermissionService.is_tournament_organizer(tournament_url, current_user):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    to_id = request.form.get("to_id")
    if not to_id:
        return jsonify({"success": False, "error": "TO ID is required"}), 400

    # Get the TO entry to remove
    to_to_remove = TO.query.get_or_404(to_id)

    # Verify it's for this tournament
    if to_to_remove.event != tournament_url:
        return jsonify({"success": False, "error": "Invalid TO entry"}), 400

    # Prevent removing yourself (optional - you might want to allow this)
    if to_to_remove.user_id == current_user.id and to_to_remove.user_type == current_user_type():
        return (
            jsonify({"success": False, "error": "You cannot remove yourself as a TO"}),
            400,
        )

    # Get user info for flash message
    if to_to_remove.user_type == "player":
        user = Player.query.get(to_to_remove.user_id)
    else:
        user = Team.query.get(to_to_remove.user_id)
    user_name = user.name if user else to_to_remove.user_id

    # Delete the TO entry
    db.session.delete(to_to_remove)
    db.session.commit()

    return (
        jsonify({"success": True, "message": f"Successfully removed {user_name} as a TO"}),
        200,
    )


@bp.route("/<tournament_url>/add-field", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def add_field(tournament_url):
    """Add a field to tournament."""
    Tournament.query.filter_by(url=tournament_url).first_or_404()

    field = Field(event=tournament_url, name=request.form["field_name"])

    db.session.add(field)
    db.session.commit()

    return jsonify({"success": True, "message": "Field added successfully!"}), 200


@bp.route("/<tournament_url>/update-field", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def update_field(tournament_url):
    """Update field."""
    field_id = request.form.get("field_id")
    if not field_id:
        return jsonify({"success": False, "error": "Field ID is required"}), 400

    field = Field.query.get_or_404(field_id)
    old_field_name = field.name
    new_field_name = request.form["field_name"]

    # Update field name
    field.name = new_field_name

    # Propagate field name change to all matches that reference this field
    name_update_count = 0
    if old_field_name != new_field_name:
        matches_to_update = Match.query.filter_by(event=tournament_url, field=old_field_name).all()
        for match in matches_to_update:
            match.field = new_field_name
            name_update_count += 1

    msg = (
        f"Field updated successfully! Updated {name_update_count} match(es) to use the new field name."
        if name_update_count > 0
        else "Field updated successfully!"
    )
    db.session.commit()
    return jsonify({"success": True, "message": msg}), 200


@bp.route("/<tournament_url>/delete-field", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def delete_field(tournament_url):
    """Delete field."""
    field_id = request.form.get("field_id")
    if not field_id:
        return jsonify({"success": False, "error": "Field ID is required"}), 400

    field = Field.query.get_or_404(field_id)
    db.session.delete(field)
    db.session.commit()
    return jsonify({"success": True, "message": "Field deleted successfully!"}), 200


@bp.route("/<tournament_url>/add-tag", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def add_tag(tournament_url):
    """Add a tag to tournament."""
    tag = Tag(event=tournament_url, name=request.form["tag_name"])

    db.session.add(tag)
    db.session.commit()

    return jsonify({"success": True, "message": "Tag added successfully!"}), 200


@bp.route("/<tournament_url>/delete-tag", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def delete_tag(tournament_url):
    """Delete tag."""
    tag_id = request.form.get("tag_id")
    if not tag_id:
        return jsonify({"success": False, "error": "Tag ID is required"}), 400

    tag = Tag.query.get_or_404(tag_id)
    from app.services.bracket_cleanup_service import scrub_deleted_tags

    if tag.event:
        scrub_deleted_tags(tag.event, [tag.name])
    db.session.delete(tag)
    db.session.commit()
    return jsonify({"success": True, "message": "Tag deleted successfully!"}), 200
