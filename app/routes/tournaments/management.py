"""TO-side tournament and league management routes.

Create / edit / delete tournaments and leagues, field CRUD, tag CRUD,
TO membership management. Part of the ``tournaments`` blueprint.
"""

from flask import (
    request,
    jsonify,
)
from flask_login import login_required, current_user


from models import (
    Tournament,
    Match,
    Field,
    ScheduleItem,
    Tag,
    db,
)
from app.services._common import current_user_type
from app.services.league_management_service import LeagueManagementService
from app.services.tournament_management_service import TournamentManagementService
from app.utils.decorators import require_tournament_organizer
from app.utils.result_helpers import json_from_result
from app.utils.settings_save import SettingsSaveError, save_tournament_settings
from app.utils.profile_banner_helpers import (
    BannerUploadError,
    read_banner_upload,
    remove_banner,
    replace_banner,
)


from app.services.permission_service import PermissionService
from . import bp


@bp.route("/tournaments/<tournament_url>/banner-photo", methods=["POST", "DELETE"])
@login_required
def tournament_banner_photo(tournament_url):
    """Replace or remove a banner using the tournament's typed TO permissions."""
    tournament = Tournament.query.get_or_404(tournament_url)
    if not PermissionService.is_tournament_organizer_of(tournament, current_user):
        return jsonify({"error": "Only tournament organizers can change this banner."}), 403
    try:
        if request.method == "DELETE":
            remove_banner(tournament, "tournament", tournament.url)
            return jsonify({"success": True})
        path = replace_banner(tournament, "tournament", tournament.url, read_banner_upload())
        return jsonify({"success": True, "path": path})
    except BannerUploadError as error:
        return jsonify({"error": str(error)}), error.status


@bp.route("/create-tournament", methods=["POST"])
@login_required
def create_tournament():
    """Create a new tournament and assign the creator as TO.

    ``POST /_api/create-tournament``

    Form Data:
        name (str): Display name for the tournament.
        url (str): URL slug (must be unique).
        league_id (str | None): Optional league to attach to; the caller
            must organize it.

    Returns:
        JSON ``{"success": true, "url": "<slug>"}`` on success, or error
        with HTTP 400/403.
    """
    result = TournamentManagementService.create_tournament(
        actor_user_id=current_user.id,
        actor_user_type=current_user_type(),
        name=request.form["name"],
        url=request.form["url"],
        league_id=request.form.get("league_id", ""),
    )
    return json_from_result(
        result,
        ok_to_payload=lambda tournament: {
            "message": f'Tournament "{tournament.name}" created successfully!',
            "url": tournament.url,
        },
    )


@bp.route("/create-league", methods=["POST"])
@login_required
def create_league():
    """Create a new league. TOs create a new league for each season."""
    result = LeagueManagementService.create_league(
        actor_user_id=current_user.id,
        actor_user_type=current_user_type(),
        name=request.form.get("league_name", ""),
        url=request.form.get("league_url", ""),
    )
    return json_from_result(
        result,
        ok_to_payload=lambda league: {
            "message": f'League "{league.name}" created successfully!',
            "league_url": league.url,
        },
    )


@bp.route("/<tournament_url>/update-settings", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def update_tournament_settings(tournament_url):
    """Update tournament settings."""
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    if tournament.historical:
        return jsonify({"success": False, "error": "Edit historical tournaments from the records editor."}), 400

    try:
        save_tournament_settings(tournament)
    except SettingsSaveError as error:
        return jsonify({"success": False, "error": str(error)}), error.status
    return (
        jsonify({"success": True, "message": "Tournament settings updated successfully!"}),
        200,
    )


@bp.route("/<tournament_url>/delete", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def delete_tournament(tournament_url):
    """Delete a tournament and all related data."""
    Tournament.query.filter_by(url=tournament_url).first_or_404()

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

    return json_from_result(
        TournamentManagementService.delete_tournament(tournament_url),
        ok_to_payload=lambda name: {"message": f'Tournament "{name}" has been permanently deleted.'},
    )


@bp.route("/<tournament_url>/add-to", methods=["POST"])
@login_required
def add_to(tournament_url):
    """Add a TO to the tournament."""
    Tournament.query.filter_by(url=tournament_url).first_or_404()
    if not PermissionService.is_tournament_organizer(tournament_url, current_user):
        return jsonify({"success": False, "error": "Only tournament organizers can access this page"}), 403

    result = TournamentManagementService.add_organizer(
        tournament_url, request.form.get("user_id", ""), request.form.get("user_type", "")
    )
    return json_from_result(result, ok_to_payload=lambda name: {"message": f"Successfully added {name} as a TO"})


@bp.route("/<tournament_url>/remove-to", methods=["POST"])
@login_required
def remove_to(tournament_url):
    """Remove a TO from the tournament."""
    Tournament.query.filter_by(url=tournament_url).first_or_404()
    if not PermissionService.is_tournament_organizer(tournament_url, current_user):
        return jsonify({"success": False, "error": "Only tournament organizers can access this page"}), 403

    to_id = request.form.get("to_id")
    if not to_id:
        return jsonify({"success": False, "error": "TO ID is required"}), 400

    result = TournamentManagementService.remove_organizer(
        tournament_url, to_id, actor_user_id=current_user.id, actor_user_type=current_user_type()
    )
    return json_from_result(result, ok_to_payload=lambda name: {"message": f"Successfully removed {name} as a TO"})


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

    field = Field.query.filter_by(id=field_id, event=tournament_url).first_or_404()
    old_field_name = field.name
    new_field_name = request.form["field_name"]

    # Update field name
    field.name = new_field_name

    # Propagate field name change to all matches that reference this field
    name_update_count = 0
    if old_field_name != new_field_name:
        ScheduleItem.query.filter_by(event=tournament_url, field=old_field_name).update({"field": new_field_name})
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

    field = Field.query.filter_by(id=field_id, event=tournament_url).first_or_404()
    ScheduleItem.query.filter_by(event=tournament_url, field=field.name).update({"field": None})
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
