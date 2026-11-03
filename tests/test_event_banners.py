"""Event banners retain publication visibility and typed organizer permissions."""

from io import BytesIO

from PIL import Image
import pytest

from app.serializers.api_v1_serializer import league_json, tournament_json
from app.utils import profile_banner_helpers as banners
from models import League, Player, Team, TO, db
from tests.utils import login_as, make_registrable_config

pytestmark = pytest.mark.integration


def image_bytes(mode="RGB"):
    output = BytesIO()
    Image.new(mode, (600, 200), (90, 120, 150, 100) if mode == "RGBA" else (90, 120, 150)).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def storage(app, tmp_path, monkeypatch):
    root = tmp_path / "static"
    root.mkdir()
    monkeypatch.setattr(app, "static_folder", str(root))
    return root


@pytest.fixture(params=["tournament", "league", "league_tournament"])
def banner_event(request, tournament):
    league = League(
        url=tournament.url,
        name="League",
        published=True,
        registrable_config_id=make_registrable_config().id,
    )
    db.session.add(league)
    if request.param == "league_tournament":
        tournament.league_id = league.url
        tournament.registrable_config_id = None
    db.session.commit()
    if request.param == "league":
        return "league", league, {"league_id": league.url}
    scope = {"league_id": league.url} if tournament.league_id else {"event": tournament.url}
    return "tournament", tournament, scope


def endpoint(kind, entity):
    return f"/_api/{kind}s/{entity.url}"


def assign_organizer(actor, scope):
    db.session.add(TO(user_id=actor.id, user_type="player" if isinstance(actor, Player) else "team", **scope))
    db.session.commit()


@pytest.mark.parametrize("actor_kind", ["player", "team"])
@pytest.mark.parametrize("published", [False, True])
def test_organizer_can_replace_remove_banner_without_changing_visibility(
    app, client, banner_event, player, team, storage, actor_kind, published
):
    kind, entity, scope = banner_event
    actor = player if actor_kind == "player" else team
    entity.published = published
    assign_organizer(actor, scope)
    login_as(client, actor)
    url = endpoint(kind, entity)
    assert client.get(url).get_json()[kind]["banner_photo"] is None

    first = client.post(url + "/banner-photo", data=image_bytes(), content_type="image/png")
    assert first.status_code == 200, first.get_json()
    first_path = first.get_json()["path"]
    assert first_path.startswith(f"uploads/banners/{kind}-")
    with Image.open(storage / first_path) as image:
        assert image.format == "JPEG" and image.size == (600, 200)
    assert client.get("/static/" + first_path).mimetype == "image/jpeg"
    assert client.get(url).get_json()[kind]["banner_photo"] == first_path
    public_serializer = tournament_json if kind == "tournament" else league_json
    assert public_serializer(entity)["banner_photo"] == "/static/" + first_path

    second = client.post(url + "/banner-photo", data=image_bytes("RGBA"), content_type="image/png")
    assert second.status_code == 200
    second_path = second.get_json()["path"]
    assert first_path != second_path and second_path.endswith(".png")
    assert not (storage / first_path).exists()
    assert entity.published is published
    # A separate browser and app context isolate guest cookies and Flask-Login's
    # cached principal from the fixture's persistent organizer context.
    with app.app_context(), app.test_client() as guest:
        assert guest.get("/_api/me").status_code == 401
        response = guest.get(url)
        # Tournament reads mask private records as absent; league reads use 403.
        expected_status = 200 if published else (404 if kind == "tournament" else 403)
        assert response.status_code == expected_status
        if published:
            assert response.get_json()[kind]["banner_photo"] == second_path
        listing = guest.get(f"/_api/{kind}s").get_json()[f"{kind}s"]
        id_key = "url" if kind == "tournament" else "league_url"
        listed = next((item for item in listing if item[id_key] == entity.url), None)
        assert bool(listed) is published
        if listed:
            assert listed["banner_photo"] == second_path

    assert client.delete(url + "/banner-photo").get_json() == {"success": True}
    assert client.delete(url + "/banner-photo").status_code == 200
    assert not (storage / second_path).exists()
    assert client.get(url).get_json()[kind]["banner_photo"] is None
    assert entity.published is published


@pytest.mark.parametrize("actor_kind", ["player", "team"])
@pytest.mark.parametrize("viewer", ["guest", "unrelated", "wrong_account_type"])
@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_changes_require_typed_organizer(
    app, client, banner_event, player, team, storage, monkeypatch, actor_kind, viewer, method
):
    kind, entity, scope = banner_event
    organizer = player if actor_kind == "player" else team
    entity.banner_photo = "uploads/banners/existing.jpg"
    assign_organizer(organizer, scope)
    if viewer != "guest":
        model = (Team if actor_kind == "player" else Player) if viewer == "wrong_account_type" else type(organizer)
        actor = model(id=organizer.id if viewer == "wrong_account_type" else "unrelated", name="Other", pw_hash="x")
        db.session.add(actor)
        db.session.commit()
        login_as(client, actor)
        if viewer == "wrong_account_type":
            # Isolate typed TO authorization from the legacy ID-only session loader.
            monkeypatch.setattr(app.login_manager, "_user_callback", lambda _id: actor)
    response = client.open(
        endpoint(kind, entity) + "/banner-photo", method=method, data=image_bytes(), content_type="image/png"
    )
    assert response.status_code == (401 if viewer == "guest" else 403)
    assert entity.banner_photo == "uploads/banners/existing.jpg"
    assert not (storage / "uploads").exists()


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_organizer_assignment_does_not_cross_event_and_league_scopes(client, banner_event, player, storage, method):
    kind, entity, scope = banner_event
    # Both entities deliberately have the same slug; only the correct scope grants access.
    other_scope = {"event": entity.url} if "league_id" in scope else {"league_id": entity.url}
    assign_organizer(player, other_scope)
    login_as(client, player)
    response = client.open(
        endpoint(kind, entity) + "/banner-photo", method=method, data=image_bytes(), content_type="image/png"
    )
    assert response.status_code == 403
    assert entity.banner_photo is None


@pytest.mark.parametrize("invalid_case,status", [("svg", 400), ("oversize", 413)])
def test_invalid_event_banner_preserves_existing_image(client, banner_event, player, storage, invalid_case, status):
    kind, entity, scope = banner_event
    assign_organizer(player, scope)
    login_as(client, player)
    url = endpoint(kind, entity) + "/banner-photo"
    original = client.post(url, data=image_bytes(), content_type="image/png").get_json()["path"]
    data = b"<svg/>" if invalid_case == "svg" else b"x" * (banners.MAX_BANNER_BYTES + 1)
    response = client.post(url, data=data, content_type="image/png")
    assert response.status_code == status and response.get_json()["error"]
    assert entity.banner_photo == original and (storage / original).exists()


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_cleanup_cannot_remove_another_kind_with_same_id(client, banner_event, player, storage, method):
    kind, entity, scope = banner_event
    other_kind = "league" if kind == "tournament" else "tournament"
    directory = banners._banner_directory(other_kind, entity.url)
    directory.mkdir(parents=True)
    other = directory / ("a" * 32 + ".jpg")
    other.write_bytes(b"other banner")
    entity.banner_photo = other.relative_to(storage).as_posix()
    assign_organizer(player, scope)
    login_as(client, player)
    response = client.open(
        endpoint(kind, entity) + "/banner-photo", method=method, data=image_bytes(), content_type="image/png"
    )
    assert response.status_code == 200
    assert other.read_bytes() == b"other banner"


@pytest.mark.parametrize("kind", ["tournament", "league"])
@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_missing_event_returns_not_found(client, player, kind, method):
    login_as(client, player)
    assert client.open(f"/_api/{kind}s/missing/banner-photo", method=method).status_code == 404
