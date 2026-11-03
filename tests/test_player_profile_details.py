"""Optional public player details share the existing atomic profile save."""

from io import BytesIO
import json

from PIL import Image
import pytest

from app.domain.enums import Pompfen
from app.serializers.api_v1_serializer import player_profile_json
from models import Player, Team, db
from tests.utils import login_as

pytestmark = pytest.mark.integration


@pytest.fixture(params=["json", "multipart"])
def save(client, player, request):
    def update(data):
        if request.param == "json":
            return client.put(f"/_api/players/{player.id}", json=data)
        return client.put(
            f"/_api/players/{player.id}",
            data={"profile": json.dumps(data)},
            content_type="multipart/form-data",
        )

    return update


def test_defaults_are_public_empty_and_team_profiles_unchanged(client, player, team):
    public = client.get(f"/_api/players/{player.id}").get_json()["player"]
    assert public["pronouns"] is None and public["pompfen"] == []
    assert player.pronouns is None and player.pompfen == []
    team_public = client.get(f"/_api/teams/{team.id}").get_json()["team"]
    assert "pronouns" not in team_public and "pompfen" not in team_public


def test_owner_can_save_clear_and_keep_public_details(client, player, save):
    login_as(client, player)
    assert save({"pronouns": "  they / she  ", "pompfen": ["SKULL", "CHAIN", "STAFF"]}).status_code == 200
    db.session.refresh(player)
    assert player.pronouns == "they / she" and player.pompfen == ["CHAIN", "STAFF", "SKULL"]
    public = client.get(f"/_api/players/{player.id}").get_json()["player"]
    versioned = player_profile_json(player)
    for profile in (public, versioned):
        assert profile["pronouns"] == "they / she" and profile["pompfen"] == ["CHAIN", "STAFF", "SKULL"]
    assert save({"bio": "An updated bio"}).status_code == 200
    assert player.pronouns == "they / she" and player.pompfen == ["CHAIN", "STAFF", "SKULL"]
    assert save({"pronouns": "   ", "pompfen": []}).status_code == 200
    assert player.pronouns is None and player.pompfen == []
    assert save({"pronouns": "x" * 64, "pompfen": [choice.name for choice in Pompfen]}).status_code == 200
    assert save({"pronouns": None}).status_code == 200
    assert player.pronouns is None and len(player.pompfen) == 8


@pytest.mark.parametrize(
    "invalid",
    [
        {"pronouns": "x" * 65},
        {"pronouns": ["they", "them"]},
        {"pronouns": 42},
        {"pompfen": None},
        {"pompfen": "CHAIN"},
        {"pompfen": ["chain"]},
        {"pompfen": ["UNKNOWN"]},
        {"pompfen": ["CHAIN", "CHAIN"]},
        {"pompfen": [1]},
        {"pompfen": [["CHAIN"]]},
        {"pompfen": ["CHAIN"] * 9},
    ],
)
def test_invalid_details_do_not_partially_save_text(client, player, save, invalid):
    player.pronouns, player.pompfen = "he / they", ["LONG"]
    db.session.commit()
    login_as(client, player)
    response = save({"name": "Must not save", "pronouns": "she / her", "pompfen": ["CHAIN"]} | invalid)
    assert response.status_code == 400 and response.get_json()["error"]
    db.session.refresh(player)
    assert player.name == "Test Player" and player.pronouns == "he / they" and player.pompfen == ["LONG"]


@pytest.mark.parametrize("viewer", ["guest", "unrelated", "wrong_type"])
def test_only_typed_owner_can_edit_details(app, client, player, save, monkeypatch, viewer):
    if viewer != "guest":
        actor = (
            Team(id=player.id, name="Same ID team")
            if viewer == "wrong_type"
            else Player(id="someone-else", name="Other player")
        )
        db.session.add(actor)
        db.session.commit()
        login_as(client, actor)
        if viewer == "wrong_type":
            monkeypatch.setattr(app.login_manager, "_user_callback", lambda _id: actor)
    response = save({"pronouns": "they / them", "pompfen": ["BOARD"]})
    assert response.status_code == (401 if viewer == "guest" else 403)
    db.session.refresh(player)
    assert player.pronouns is None and player.pompfen == []


def test_details_and_images_roll_back_together(app, client, player, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    player.pronouns, player.pompfen = "they / them", ["QTIP"]
    db.session.commit()
    login_as(client, player)
    output = BytesIO()
    Image.new("RGB", (80, 80)).save(output, format="PNG")

    def fail_commit():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db.session, "commit", fail_commit)
    response = client.put(
        f"/_api/players/{player.id}",
        data={
            "profile": json.dumps({"pronouns": "she / her", "pompfen": ["FLORENTINE"]}),
            "profile_photo": (BytesIO(output.getvalue()), "avatar.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 500
    db.session.refresh(player)
    assert player.pronouns == "they / them" and player.pompfen == ["QTIP"] and player.profile_photo is None
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_invalid_image_keeps_new_details_unchanged(client, player):
    login_as(client, player)
    response = client.put(
        f"/_api/players/{player.id}",
        data={
            "profile": json.dumps({"pronouns": "she / her", "pompfen": ["FLORENTINE"]}),
            "banner_photo": (BytesIO(b"not an image"), "cover.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    db.session.refresh(player)
    assert player.pronouns is None and player.pompfen == []
