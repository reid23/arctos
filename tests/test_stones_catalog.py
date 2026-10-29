"""The Stones sound catalog applies the configured account allowlist."""

import pytest

from tests.utils import login_as

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("viewer", "allowlist", "extra_sounds"),
    [
        (None, None, False),
        ("player", None, False),
        (None, "{player_id}:{team_id}", False),
        ("player", "someone-else", False),
        ("player", "{player_name}", False),
        ("player", "prefix-{player_id}:{player_id}-suffix", False),
        ("player", "{player_id_upper}", False),
        ("player", "another-account:{player_id}:{team_id}", True),
        ("team", "another-account:{player_id}:{team_id}", True),
    ],
)
def test_extra_stone_sounds_require_an_allowlisted_authenticated_account(
    client, player, team, monkeypatch, viewer, allowlist, extra_sounds
):
    if allowlist is None:
        monkeypatch.delenv("SILLY_USERS", raising=False)
    else:
        monkeypatch.setenv(
            "SILLY_USERS",
            allowlist.format(
                player_id=player.id,
                player_name=player.name,
                player_id_upper=player.id.upper(),
                team_id=team.id,
            ),
        )
    if viewer:
        login_as(client, player if viewer == "player" else team)

    response = client.get("/_api/stones")
    assert response.status_code == 200
    names = [sound["display_name"] for sound in response.get_json()["stones"]]
    assert names[:2] == ["Classic", "Snare"]
    if extra_sounds:
        assert "Quack" in names
    else:
        assert names == ["Classic", "Snare"]


def test_stone_catalog_uses_the_current_session_after_sign_in_and_sign_out(client, player, monkeypatch):
    monkeypatch.setenv("SILLY_USERS", player.id)

    def names():
        response = client.get("/_api/stones")
        assert response.status_code == 200
        return [sound["display_name"] for sound in response.get_json()["stones"]]

    assert names() == ["Classic", "Snare"]
    login = client.post("/_api/login", json={"username": player.id, "password": "testpass"})
    assert login.status_code == 200
    assert "Quack" in names()
    assert client.post("/_api/logout").status_code == 200
    assert names() == ["Classic", "Snare"]
