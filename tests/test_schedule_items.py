"""Activities share schedule visibility without becoming matches."""

from datetime import datetime, timedelta

import pytest

from app.domain.enums import MatchStatus, ScheduleType, SideCompType
from app.services.schedule_import_export_service import ScheduleImportExportService
from models import Field, Match, ScheduleItem, SideComp, TO, Tournament, db
from tests.utils import login_as, make_registrable_config

pytestmark = pytest.mark.integration


def organizer(client, tournament, player):
    db.session.add(TO(event=tournament.url, user_id=player.id, user_type="player"))
    db.session.commit()
    login_as(client, player)


def payload(**changes):
    return {
        "title": "Announcements",
        "description": "Meet at the registration desk.",
        "start_time": "2026-11-01T00:30:00-07:00",
        "end_time": "2026-11-01T02:30:00-08:00",
        **changes,
    }


def base(tournament):
    return f"/_api/tournaments/{tournament.url}/schedule-items"


def test_activity_crud_does_not_create_matches(client, tournament, player):
    organizer(client, tournament, player)
    response = client.post(base(tournament), json=payload())
    assert response.status_code == 201
    item = response.get_json()
    assert item["start_time"] == "2026-11-01T07:30:00Z"
    assert item["end_time"] == "2026-11-01T10:30:00Z"
    assert Match.query.count() == 0
    item_url = f"{base(tournament)}/{item['id']}"
    assert client.put(item_url, json=payload(title="Team meeting")).get_json()["title"] == "Team meeting"
    for suffix in ("schedule", "schedule-setup"):
        listed = client.get(f"/_api/tournaments/{tournament.url}/{suffix}").get_json()["schedule_items"]
        assert [i["title"] for i in listed] == ["Team meeting"]
    assert client.delete(item_url).status_code == 200
    assert client.get(item_url).status_code == 404


def test_field_activity_does_not_reserve_time_or_move_with_matches(client, tournament, player):
    organizer(client, tournament, player)
    start = datetime(2026, 11, 1, 10)
    match = Match(
        name="Opening match",
        event=tournament.url,
        field="Field 1",
        nominal_start_time=start,
        scheduled_start_time=start,
        nominal_length=60,
        schedule_type=ScheduleType.STATIC,
        status=MatchStatus.NOT_STARTED,
    )
    db.session.add(match)
    db.session.commit()

    response = client.post(
        base(tournament),
        json=payload(
            field="Field 1",
            start_time="2026-11-01T10:00:00Z",
            end_time="2026-11-01T11:00:00Z",
        ),
    )
    assert response.status_code == 201
    item_url = f"{base(tournament)}/{response.get_json()['id']}"
    db.session.refresh(match)
    assert match.nominal_start_time == match.scheduled_start_time == start

    shifted = client.post(
        f"/_api/tournaments/{tournament.url}/push-back-matches",
        json={"day": "2026-11-01", "minutes": 30, "tz_offset_minutes": 0},
    )
    assert shifted.status_code == 200
    db.session.refresh(match)
    assert match.nominal_start_time == match.scheduled_start_time == start + timedelta(minutes=30)
    activity = client.get(item_url).get_json()
    assert activity["field"] == "Field 1"
    assert activity["start_time"] == "2026-11-01T10:00:00Z"
    assert activity["end_time"] == "2026-11-01T11:00:00Z"


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"title": "x" * 201},
        {"description": 12},
        {"start_time": "2026-11-01T07:30:00"},
        {"start_time": "not-a-date"},
        {"end_time": "2026-11-01T07:30:00Z"},
        {"end_time": "2026-10-31T10:00:00Z"},
        {"field": "Another event's field"},
        {"sidecomp_id": True},
    ],
)
def test_invalid_activity_is_rejected_without_writes(client, tournament, player, changes):
    organizer(client, tournament, player)
    assert client.post(base(tournament), json=payload(**changes)).status_code == 400
    assert ScheduleItem.query.count() == 0


@pytest.mark.parametrize("actor", ["guest", "player"])
def test_only_organizer_can_write(client, tournament, player, actor):
    organizer(client, tournament, player)
    item_id = client.post(base(tournament), json=payload()).get_json()["id"]
    TO.query.delete()
    db.session.commit()
    if actor == "guest":
        with client.session_transaction() as session:
            session.clear()
        client.post("/_api/logout")
    for method, url, body in (
        ("post", base(tournament), payload()),
        ("put", f"{base(tournament)}/{item_id}", payload()),
        ("delete", f"{base(tournament)}/{item_id}", None),
    ):
        response = getattr(client, method)(url, json=body)
        assert response.status_code == (401 if actor == "guest" else 403)
    assert ScheduleItem.query.count() == 1


def test_linked_competition_content_is_live_and_scoped(client, tournament, player):
    organizer(client, tournament, player)
    comp = SideComp(event=tournament.url, name="Dueling", type=SideCompType.DUELING, description="Original rules")
    other = Tournament(
        url="other",
        name="Other",
        start_date=datetime(2026, 11, 1),
        published=True,
        registrable_config_id=make_registrable_config().id,
    )
    db.session.add_all([comp, other])
    db.session.flush()
    foreign = SideComp(event=other.url, name="Foreign", type=SideCompType.OTHER)
    db.session.add(foreign)
    db.session.commit()
    assert client.post(base(tournament), json=payload(sidecomp_id=foreign.id)).status_code == 400
    response = client.post(base(tournament), json=payload(sidecomp_id=comp.id, title="Ignored copy"))
    item_id = response.get_json()["id"]
    assert ScheduleItem.query.get(item_id).title is None
    comp.name = "Dueling finals"
    comp.description = "Current rules"
    db.session.commit()
    detail = client.get(f"{base(tournament)}/{item_id}").get_json()
    assert (detail["title"], detail["description"]) == ("Dueling finals", "Current rules")
    db.session.add(TO(event=other.url, user_id=player.id, user_type="player"))
    db.session.commit()
    assert client.put(f"{base(other)}/{item_id}", json=payload()).status_code == 404
    assert client.delete(f"{base(other)}/{item_id}").status_code == 404
    assert client.delete(f"/_api/sidecomps/{comp.id}").status_code == 200
    assert ScheduleItem.query.count() == 0


@pytest.mark.parametrize(
    "published, event_published, expected", [(False, True, 403), (True, False, 404), (True, True, 200)]
)
def test_activity_details_follow_public_schedule_access(
    client, tournament, player, published, event_published, expected
):
    organizer(client, tournament, player)
    item_id = client.post(base(tournament), json=payload()).get_json()["id"]
    tournament.schedule_published = published
    tournament.published = event_published
    db.session.commit()
    assert client.get(f"{base(tournament)}/{item_id}").status_code == 200
    with client.session_transaction() as session:
        session.clear()
    client.post("/_api/logout")
    assert client.get(f"{base(tournament)}/{item_id}").status_code == expected


def test_new_competition_and_schedule_save_atomically(client, tournament, player):
    organizer(client, tournament, player)
    url = f"/_api/{tournament.url}/sidecomps"
    comp_data = {"name": "Dueling", "type": "DUELING", "description": "Rules", "schedule": payload(end_time="invalid")}
    assert client.post(url, json=comp_data).status_code == 400
    assert SideComp.query.count() == ScheduleItem.query.count() == 0
    comp_data["schedule"] = payload()
    created = client.post(url, json=comp_data).get_json()
    item = ScheduleItem.query.one()
    assert item.sidecomp_id == created["id"]
    assert item.title is None and item.description is None
    assert client.get(f"/_api/sidecomps/{created['id']}").get_json()["schedule_items"][0]["id"] == item.id


def test_import_roundtrip_and_older_import_preserve_activities(client, tournament, player):
    organizer(client, tournament, player)
    field = Field.query.filter_by(event=tournament.url).first()
    original = client.post(base(tournament), json=payload(field=field.name)).get_json()
    exported = ScheduleImportExportService.export_schedule(tournament.url).unwrap()
    assert "[[schedule_items]]" in exported
    assert ScheduleImportExportService.import_schedule(tournament.url, exported).is_ok()
    assert ScheduleItem.query.count() == 1
    assert ScheduleItem.query.one().id == original["id"]
    # Older files have no activity section; deleting their fields retains the activity.
    assert ScheduleImportExportService.import_schedule(tournament.url, "").is_ok()
    assert ScheduleItem.query.one().field is None
    assert ScheduleImportExportService.import_schedule(tournament.url, "schedule_items = []").is_ok()
    assert ScheduleItem.query.count() == 0


def test_invalid_import_cannot_replace_schedule(client, tournament, player):
    organizer(client, tournament, player)
    client.post(base(tournament), json=payload())
    exported = ScheduleImportExportService.export_schedule(tournament.url).unwrap()
    invalid = exported.replace('end_time = "2026-11-01T10:30:00Z"', 'end_time = "2020-01-01T00:00:00Z"')
    assert ScheduleImportExportService.import_schedule(tournament.url, invalid).is_err()
    assert ScheduleItem.query.one().end_time == datetime(2026, 11, 1, 10, 30)


@pytest.mark.parametrize("legacy", [False, True])
def test_field_changes_keep_activity_visible(client, tournament, player, legacy):
    organizer(client, tournament, player)
    field = Field.query.filter_by(event=tournament.url).first()
    client.post(base(tournament), json=payload(field=field.name))
    if legacy:
        response = client.post(
            f"/_api/{tournament.url}/update-field", data={"field_id": field.id, "field_name": "Practice field"}
        )
    else:
        response = client.put(f"/_api/tournaments/{tournament.url}/fields/{field.id}", json={"name": "Practice field"})
    assert response.status_code == 200
    assert ScheduleItem.query.one().field == "Practice field"
    if legacy:
        response = client.post(f"/_api/{tournament.url}/delete-field", data={"field_id": field.id})
    else:
        response = client.delete(f"/_api/tournaments/{tournament.url}/fields/{field.id}")
    assert response.status_code == 200
    assert ScheduleItem.query.one().field is None


@pytest.mark.parametrize(
    "event_published,schedule_published,visible", [(False, True, False), (True, False, False), (True, True, True)]
)
def test_sidecomp_detail_does_not_expose_unpublished_schedule(
    client, tournament, player, event_published, schedule_published, visible
):
    organizer(client, tournament, player)
    created = client.post(
        f"/_api/{tournament.url}/sidecomps", json={"name": "Dueling", "type": "DUELING", "schedule": payload()}
    ).get_json()
    tournament.published = event_published
    tournament.schedule_published = schedule_published
    db.session.commit()
    client.post("/_api/logout")
    items = client.get(f"/_api/sidecomps/{created['id']}").get_json()["schedule_items"]
    assert bool(items) is visible


@pytest.mark.parametrize("invalid", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:00-01:00"])
def test_out_of_range_timezone_dates_return_validation_error(client, tournament, player, invalid):
    organizer(client, tournament, player)
    assert client.post(base(tournament), json=payload(start_time=invalid)).status_code == 400
    assert ScheduleItem.query.count() == 0


def test_deleting_event_cleans_up_activities(client, tournament, player):
    organizer(client, tournament, player)
    client.post(base(tournament), json=payload())
    response = client.post(f"/_api/{tournament.url}/delete", data={"confirm_url": tournament.url})
    assert response.status_code == 200, response.get_json()
    assert ScheduleItem.query.count() == 0


@pytest.mark.parametrize("new_competition", [False, True])
def test_retry_after_lost_response_reuses_created_item(client, tournament, player, new_competition):
    organizer(client, tournament, player)
    data = payload(request_id="db8ccaf4-3e0c-4c38-82ba-7d01ce101220")
    url = base(tournament)
    if new_competition:
        data = {"name": "Dueling", "type": "DUELING", "schedule": data}
        url = f"/_api/{tournament.url}/sidecomps"
    first = client.post(url, json=data)
    retried = client.post(url, json=data)
    assert first.status_code in (200, 201)
    assert retried.status_code == 200
    assert first.get_json()["id"] == retried.get_json()["id"]
    assert ScheduleItem.query.count() == 1
    assert SideComp.query.count() == (1 if new_competition else 0)


def test_activity_duration_limit_prevents_unbounded_calendar_ranges(client, tournament, player):
    organizer(client, tournament, player)
    response = client.post(base(tournament), json=payload(end_time="9999-12-31T23:59:00Z"))
    assert response.status_code == 400
    assert "31 days" in response.get_json()["error"]
    assert ScheduleItem.query.count() == 0


def test_activity_description_roundtrips_quotes_and_control_characters(client, tournament, player):
    organizer(client, tournament, player)
    description = 'Meet at "registration".\nBring a team list.\r\nCheck in\tbefore playing.'
    client.post(base(tournament), json=payload(description=description))
    exported = ScheduleImportExportService.export_schedule(tournament.url).unwrap()
    assert ScheduleImportExportService.import_schedule(tournament.url, exported).is_ok()
    assert ScheduleItem.query.one().description == description


@pytest.mark.parametrize("changed", [{"title": "Changed title"}, {"start_time": "2026-10-26T10:10:00Z"}])
def test_retry_with_changed_activity_does_not_silently_discard_edits(client, tournament, player, changed):
    organizer(client, tournament, player)
    data = payload(request_id="db8ccaf4-3e0c-4c38-82ba-7d01ce101220")
    first = client.post(base(tournament), json=data)
    assert first.status_code == 201
    response = client.post(base(tournament), json={**data, **changed})
    assert response.status_code == 409
    assert "already saved" in response.get_json()["error"]
    assert ScheduleItem.query.count() == 1
    assert ScheduleItem.query.one().title == data["title"]


@pytest.mark.parametrize("changed", ["name", "time"])
def test_retry_with_changed_new_competition_requires_editing_saved_item(client, tournament, player, changed):
    organizer(client, tournament, player)
    data = {
        "name": "Dueling",
        "type": "DUELING",
        "schedule": payload(request_id="db8ccaf4-3e0c-4c38-82ba-7d01ce101220"),
    }
    url = f"/_api/{tournament.url}/sidecomps"
    assert client.post(url, json=data).status_code == 200
    if changed == "name":
        data["name"] = "Changed competition"
    else:
        data["schedule"]["start_time"] = "2026-10-26T10:10:00Z"
    assert client.post(url, json=data).status_code == 409
    assert ScheduleItem.query.count() == 1
    assert SideComp.query.one().name == "Dueling"


@pytest.mark.parametrize("competition_first", [False, True])
def test_retry_cannot_change_between_activity_and_new_competition(client, tournament, player, competition_first):
    organizer(client, tournament, player)
    activity = payload(request_id="db8ccaf4-3e0c-4c38-82ba-7d01ce101220")
    competition = {"name": "Dueling", "type": "DUELING", "schedule": activity}
    requests = [(base(tournament), activity), (f"/_api/{tournament.url}/sidecomps", competition)]
    if competition_first:
        requests.reverse()
    assert client.post(requests[0][0], json=requests[0][1]).status_code in (200, 201)
    assert client.post(requests[1][0], json=requests[1][1]).status_code == 409
    assert ScheduleItem.query.count() == 1
    assert SideComp.query.count() == (1 if competition_first else 0)


@pytest.mark.parametrize("new_competition_first", [False, True])
def test_retry_distinguishes_creating_and_linking_identical_competition(
    client, tournament, player, new_competition_first
):
    organizer(client, tournament, player)
    schedule = payload(request_id="db8ccaf4-3e0c-4c38-82ba-7d01ce101220")
    competition = {"name": "Dueling", "type": "DUELING"}
    competition_url = f"/_api/{tournament.url}/sidecomps"
    if new_competition_first:
        first = client.post(competition_url, json={**competition, "schedule": schedule})
        comp_id = first.get_json()["id"]
        response = client.post(base(tournament), json={**schedule, "sidecomp_id": comp_id})
    else:
        comp_id = client.post(competition_url, json=competition).get_json()["id"]
        first = client.post(base(tournament), json={**schedule, "sidecomp_id": comp_id})
        response = client.post(competition_url, json={**competition, "schedule": schedule})
    assert first.status_code in (200, 201)
    assert response.status_code == 409
    assert ScheduleItem.query.count() == 1
    assert ScheduleItem.query.one().sidecomp_id == comp_id
    assert SideComp.query.count() == 1
