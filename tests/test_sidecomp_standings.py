"""Standings selection, publication limits, and organizer access."""

import pytest
from flask import g

from app.domain.enums import Pompfen
from app.services.sidecomp_service import SideCompService
from models import TO, League, Player, SideComp, SideCompRegistration, SideCompResult, Team, db
from tests.utils import issue_access_token, login_as

pytestmark = pytest.mark.integration


def _add_entry(comp, player_id, weapon, wins, losses=0):
    player = Player(id=player_id, name=player_id, pw_hash="unused")
    db.session.add(player)
    db.session.flush()
    entry = SideCompRegistration(
        comp=comp.id,
        player=player.id,
        entry_number=SideCompRegistration.query.filter_by(comp=comp.id).count() + 1,
        weapon=weapon.value if weapon is not None else None,
    )
    db.session.add(entry)
    db.session.flush()
    for points in [1] * wins + [-1] * losses:
        db.session.add(SideCompResult(comp=comp.id, player=entry.id, points=points, ref="ref"))
    db.session.commit()
    return entry


@pytest.fixture
def chain_pit(tournament):
    comp = SideComp(
        event=tournament.url,
        name="Chain pit",
        type="CHAIN_BREAKING",
        only_show_top_n_results=3,
    )
    db.session.add(comp)
    db.session.commit()
    for index, wins in enumerate((9, 8, 7)):
        _add_entry(comp, f"staff-{index}", Pompfen.STAFF, wins)
    for index, wins in enumerate((6, 5, 4, 3)):
        _add_entry(comp, f"florentine-{index}", Pompfen.FLORENTINE, wins)
    _add_entry(comp, "chain-0", Pompfen.CHAIN, 3)
    _add_entry(comp, "chain-1", Pompfen.CHAIN, 2)
    _add_entry(comp, "chain-2", Pompfen.CHAIN, 1)
    _add_entry(comp, "chain-3", Pompfen.CHAIN, 0)
    _add_entry(comp, "incomplete", None, 10)
    return comp


def test_public_weapon_filter_selects_its_leader_before_the_cut(client, chain_pit):
    path = f"/_api/sidecomps/{chain_pit.id}/standings"
    initial = client.get(path).get_json()
    assert initial["showing_all_results"] is False
    chains, breaks = initial["tables"]
    assert chains["available_weapons"] == ["CHAIN"]
    assert breaks["available_weapons"] == ["STAFF", "FLORENTINE"]
    assert [row["player_id"] for row in breaks["rows"]] == ["staff-0", "staff-1", "staff-2"]

    response = client.get(path, query_string={"table": "breaks", "weapon": "FLORENTINE"})
    assert response.status_code == 200
    [filtered] = response.get_json()["tables"]
    assert filtered["id"] == "breaks"
    assert filtered["weapon"] == "FLORENTINE"
    assert filtered["sort"] == "wins"
    assert filtered["available_weapons"] == breaks["available_weapons"]
    assert [row["player_id"] for row in filtered["rows"]] == ["florentine-0"]
    assert filtered["rows"][0]["rank"] == 1
    assert filtered["personal_row"] is None
    assert "class_winners" not in filtered

    [staff] = client.get(path, query_string={"table": "breaks", "weapon": "STAFF"}).get_json()["tables"]
    assert [row["rank"] for row in staff["rows"]] == [1]
    assert [row["wins"] for row in staff["rows"]] == [9]

    [chains] = client.get(path, query_string={"table": "chains"}).get_json()["tables"]
    assert [row["player_id"] for row in chains["rows"]] == ["chain-0", "chain-1", "chain-2"]

    [empty] = client.get(path, query_string={"table": "breaks", "weapon": "LONG"}).get_json()["tables"]
    assert empty["rows"] == []
    assert empty["available_weapons"] == ["STAFF", "FLORENTINE"]


def test_sort_selection_precedes_limit_and_respects_each_tables_loss_setting(client, chain_pit):
    chain_pit.track_break_losses = True
    entry = SideCompRegistration.query.filter_by(comp=chain_pit.id, player="florentine-0").one()
    for _ in range(6):
        db.session.add(SideCompResult(comp=chain_pit.id, player=entry.id, points=-1, ref="ref"))
    for index, wins in enumerate((9, 8, 7)):
        staff = SideCompRegistration.query.filter_by(comp=chain_pit.id, player=f"staff-{index}").one()
        for _ in range(wins * 2):
            db.session.add(SideCompResult(comp=chain_pit.id, player=staff.id, points=-1, ref="ref"))
    db.session.add(SideCompResult(comp=chain_pit.id, player=entry.id, points=1, ref="ref", valid=False))
    db.session.commit()
    path = f"/_api/sidecomps/{chain_pit.id}/standings"
    selection = {"table": "breaks", "weapon": "FLORENTINE"}

    [by_percentage] = client.get(path, query_string=selection).get_json()["tables"]
    assert by_percentage["sort"] == "win_percentage"
    assert [row["player_id"] for row in by_percentage["rows"]] == ["florentine-1"]

    [by_wins] = client.get(path, query_string={**selection, "sort": "wins"}).get_json()["tables"]
    assert by_wins["sort"] == "wins"
    assert [row["player_id"] for row in by_wins["rows"]] == ["florentine-0"]
    assert by_wins["rows"][0]["rank"] == 1

    login_as(client, db.session.get(Player, "florentine-0"))
    # The fixture keeps an app context open across requests; reload its viewer.
    g.pop("_login_user", None)
    [personal] = client.get(path, query_string={**selection, "sort": "wins"}).get_json()["tables"]
    assert personal["personal_row"]["rank"] == 4
    assert personal["personal_row"]["wins"] == 6
    assert personal["personal_row"]["losses"] == 6
    assert personal["personal_row"]["win_percentage"] == 50

    [chains] = client.get(path, query_string={"table": "chains", "sort": "win_percentage"}).get_json()["tables"]
    assert chains["sort"] == "wins"
    assert chains["track_losses"] is False
    assert all(row["losses"] is None for row in chains["rows"])


def test_filtered_ties_use_entry_number_and_unplayed_entries_follow_played(client, tournament):
    comp = SideComp(event=tournament.url, name="Duels", type="DUELING", track_losses=True)
    db.session.add(comp)
    db.session.commit()
    _add_entry(comp, "unplayed", Pompfen.FLORENTINE, 0)
    _add_entry(comp, "early", Pompfen.FLORENTINE, 2, 2)
    _add_entry(comp, "late", Pompfen.FLORENTINE, 2, 2)
    _add_entry(comp, "busy", Pompfen.FLORENTINE, 3, 3)
    _add_entry(comp, "other-weapon", Pompfen.STAFF, 4)

    response = client.get(f"/_api/sidecomps/{comp.id}/standings?table=all&weapon=FLORENTINE&sort=win_percentage")
    [table] = response.get_json()["tables"]
    assert [row["player_id"] for row in table["rows"]] == ["busy", "early", "late", "unplayed"]
    assert [row["rank"] for row in table["rows"]] == [1, 2, 3, 4]
    assert table["rows"][-1]["win_percentage"] is None


def _set_viewer(client, tournament, scope, viewer_kind):
    if scope == "league":
        league = League(url="league", name="League", registrable_config_id=tournament.registrable_config_id)
        db.session.add(league)
        db.session.flush()
        tournament.league_id = league.url
        tournament.registrable_config_id = None
    if viewer_kind == "anonymous":
        db.session.commit()
        return
    if viewer_kind == "participant":
        db.session.commit()
        login_as(client, db.session.get(Player, "florentine-3"))
        return
    user_type = "team" if viewer_kind.startswith("team") else "player"
    user_model = Team if user_type == "team" else Player
    viewer = user_model(id="viewer", name="Viewer", pw_hash="unused")
    db.session.add(viewer)
    db.session.flush()
    if viewer_kind.endswith("organizer"):
        db.session.add(
            TO(
                user_id=viewer.id,
                user_type=user_type,
                event=tournament.url if scope == "tournament" else None,
                league_id=tournament.league_id if scope == "league" else None,
            )
        )
    db.session.commit()
    login_as(client, viewer)


@pytest.mark.parametrize("scope", ["tournament", "league"])
@pytest.mark.parametrize(
    "viewer_kind", ["anonymous", "player", "participant", "team", "player-organizer", "team-organizer"]
)
def test_only_organizers_receive_complete_tables(client, tournament, chain_pit, scope, viewer_kind):
    _set_viewer(client, tournament, scope, viewer_kind)
    query = {"table": "breaks", "include_below_cut": "true", "viewer_player_id": "florentine-3"}
    response = client.get(
        f"/_api/sidecomps/{chain_pit.id}/standings",
        query_string=query,
    )
    assert response.status_code == 200
    payload = response.get_json()
    is_organizer = viewer_kind.endswith("organizer")
    assert payload["showing_all_results"] is is_organizer
    assert response.cache_control.private and response.cache_control.no_store
    [table] = payload["tables"]
    count = 7 if is_organizer else 3
    assert len(table["rows"]) == count
    assert [row["rank"] for row in table["rows"]] == list(range(1, count + 1))
    if viewer_kind == "participant":
        assert table["personal_row"]["player_id"] == "florentine-3"
        assert table["personal_row"]["rank"] == 7
    else:
        assert table["personal_row"] is None


def test_participant_below_the_leaders_cut_can_find_their_rank(client, chain_pit):
    login_as(client, db.session.get(Player, "florentine-3"))
    path = f"/_api/sidecomps/{chain_pit.id}/standings"
    breaks = client.get(path).get_json()["tables"][1]
    own_row = breaks["personal_row"]
    assert own_row["rank"] == 7
    assert len(breaks["rows"]) == 3
    assert all(row["player_id"] != "florentine-3" for row in breaks["rows"])

    for weapon in ("FLORENTINE", "STAFF", "LONG"):
        [filtered] = client.get(path, query_string={"table": "breaks", "weapon": weapon}).get_json()["tables"]
        assert filtered["personal_row"] == own_row
    [other_table] = client.get(path, query_string={"table": "chains"}).get_json()["tables"]
    assert other_table["personal_row"] is None


@pytest.mark.parametrize("scope", ["tournament", "league"])
@pytest.mark.parametrize("viewer_kind", ["anonymous", "player", "team", "player-organizer", "team-organizer"])
def test_unpublished_standings_are_only_visible_to_scope_organizers(client, tournament, chain_pit, scope, viewer_kind):
    tournament.published = False
    _set_viewer(client, tournament, scope, viewer_kind)
    response = client.get(f"/_api/sidecomps/{chain_pit.id}/standings")
    if viewer_kind.endswith("organizer"):
        assert response.status_code == 200
        assert len(response.get_json()["tables"][1]["rows"]) == 7
    else:
        assert response.status_code == 404
        assert response.get_json()["error"] == "Side competition not found"
    assert response.cache_control.private and response.cache_control.no_store


def test_missing_competition_standings_return_not_found(client):
    assert client.get("/_api/sidecomps/999/standings").status_code == 404


@pytest.mark.parametrize("top_n", [None, 0, 2])
@pytest.mark.parametrize("finalized", [False, True])
def test_publication_settings_apply_in_every_state(client, chain_pit, top_n, finalized):
    chain_pit.only_show_top_n_results = top_n
    chain_pit.finalized = finalized
    db.session.commit()

    response = client.get(
        f"/_api/sidecomps/{chain_pit.id}/standings",
        query_string={"table": "breaks"},
    )
    payload = response.get_json()
    assert payload["results_page_enabled"] is (top_n != 0)
    assert payload["showing_all_results"] is (top_n is None)
    assert payload["finalized"] is finalized
    expected_count = 7 if top_n is None else top_n
    assert len(payload["tables"][0]["rows"]) == expected_count


@pytest.mark.parametrize(
    "viewer_kind", ["anonymous", "player", "participant", "team", "player-organizer", "team-organizer"]
)
def test_hidden_results_do_not_return_rows_to_any_viewer(client, tournament, chain_pit, viewer_kind):
    chain_pit.only_show_top_n_results = 0
    _set_viewer(client, tournament, "tournament", viewer_kind)
    payload = client.get(f"/_api/sidecomps/{chain_pit.id}/standings").get_json()
    assert payload["results_page_enabled"] is False
    assert payload["showing_all_results"] is False
    assert all(table["rows"] == [] for table in payload["tables"])
    assert all(table["personal_row"] is None for table in payload["tables"])
    assert all("class_winners" not in table for table in payload["tables"])


@pytest.mark.parametrize("include_below_cut", [False, True])
def test_full_service_view_cannot_reveal_hidden_results(chain_pit, include_below_cut):
    chain_pit.only_show_top_n_results = 0
    db.session.commit()
    payload = SideCompService.standings(
        chain_pit.id, include_below_cut=include_below_cut, viewer_player_id="florentine-3", application_view=True
    ).unwrap()
    assert payload["results_page_enabled"] is False
    assert payload["showing_all_results"] is False
    assert all(table["rows"] == [] for table in payload["tables"])
    assert all(table["personal_row"] is None for table in payload["tables"])
    assert all("class_winners" not in table for table in payload["tables"])


@pytest.mark.parametrize(
    "query",
    [
        {"table": "unknown"},
        {"table": "chains"},
        {"table": ""},
        {"weapon": "unknown"},
        {"weapon": "5"},
        {"weapon": ""},
        {"sort": "unknown"},
        {"sort": ""},
        {"view": ""},
        {"view": "unknown"},
        {"view": "ALL"},
        {"view": "all"},
        {"view": "leaders"},
    ],
)
def test_invalid_standings_selection_returns_validation_error(client, tournament, query):
    comp = SideComp(event=tournament.url, name="Duels", type="DUELING")
    db.session.add(comp)
    db.session.commit()

    response = client.get(f"/_api/sidecomps/{comp.id}/standings", query_string=query)
    assert response.status_code == 400
    assert "Invalid standings" in response.get_json()["error"]
    assert response.cache_control.private and response.cache_control.no_store


@pytest.mark.parametrize("viewer_kind", ["client", "participant", "organizer"])
def test_public_api_keeps_default_cap_for_every_token(client, tournament, chain_pit, viewer_kind):
    organizer = Player(id="organizer", name="Organizer", pw_hash="unused")
    db.session.add(organizer)
    db.session.flush()
    db.session.add(TO(user_id=organizer.id, user_type="player", event=tournament.url))
    db.session.commit()
    viewer = {"client": None, "participant": db.session.get(Player, "florentine-3"), "organizer": organizer}[
        viewer_kind
    ]
    token = issue_access_token(viewer)
    path = f"/api/v1/tournaments/{tournament.url}/side-competitions/{chain_pit.id}/results"
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get(path, headers=headers)
    assert response.status_code == 200
    chains, breaks = response.get_json()["tables"]
    assert set(breaks) == {"id", "title", "track_losses", "rows"}
    assert [row["player"]["id"] for row in breaks["rows"]] == ["staff-0", "staff-1", "staff-2"]
    assert len(chains["rows"]) == 3

    chain_pit.only_show_top_n_results = 0
    db.session.commit()
    assert client.get(path, headers=headers).status_code == 404


@pytest.fixture
def bear_pit(tournament):
    comp = SideComp(event=tournament.url, name="Bear pit", type="DUELING", only_show_top_n_results=3)
    db.session.add(comp)
    db.session.commit()
    for index, weapon in enumerate(Pompfen):
        _add_entry(comp, f"{weapon.name}-leader", weapon, 10 - index)
        _add_entry(comp, f"{weapon.name}-second", weapon, 0)
    _add_entry(comp, "incomplete-bear", None, 20)
    return comp


def test_bear_pit_publishes_overall_leaders_with_all_eight_competed_choices(client, bear_pit):
    payload = client.get(f"/_api/sidecomps/{bear_pit.id}/standings").get_json()
    [table] = payload["tables"]
    assert [row["player_id"] for row in table["rows"]] == ["CHAIN-leader", "LONG-leader", "QTIP-leader"]
    assert [row["rank"] for row in table["rows"]] == [1, 2, 3]
    assert table["available_weapons"] == [weapon.name for weapon in Pompfen]
    assert "class_winners" not in table
    assert table["personal_row"] is None
    assert payload["showing_all_results"] is False


@pytest.mark.parametrize("weapon", [weapon.name for weapon in Pompfen])
def test_each_competed_weapon_displays_its_leader_in_the_same_table(client, bear_pit, weapon):
    [table] = client.get(f"/_api/sidecomps/{bear_pit.id}/standings", query_string={"weapon": weapon}).get_json()[
        "tables"
    ]
    assert [row["rank"] for row in table["rows"]] == [1]
    assert [row["weapon"] for row in table["rows"]] == [weapon]
    assert table["rows"][0]["player_id"] == f"{weapon}-leader"
    assert "class_winners" not in table


def test_bear_participant_gets_only_their_own_row_beyond_public_results(client, bear_pit):
    login_as(client, db.session.get(Player, "UNARMED-second"))
    [table] = client.get(
        f"/_api/sidecomps/{bear_pit.id}/standings",
        query_string={"weapon": "LONG", "player_id": "CHAIN-second", "viewer_player_id": "CHAIN-second"},
    ).get_json()["tables"]
    assert [row["player_id"] for row in table["rows"]] == ["LONG-leader"]
    assert table["personal_row"]["player_id"] == "UNARMED-second"
    assert table["personal_row"]["rank"] == 16
    assert table["personal_row"]["wins"] == 0


@pytest.mark.parametrize("is_organizer", [False, True])
def test_team_account_cannot_receive_personal_row_for_matching_player_id(
    app, client, tournament, bear_pit, is_organizer, monkeypatch
):
    team = Team(id="UNARMED-second", name="Team", pw_hash="unused")
    db.session.add(team)
    db.session.flush()
    if is_organizer:
        db.session.add(TO(user_id=team.id, user_type="team", event=tournament.url))
    db.session.commit()
    login_as(client, team)
    # Account creation prevents collisions; isolate this boundary from the
    # legacy ID-only session loader, which loads a matching player first.
    monkeypatch.setattr(app.login_manager, "_user_callback", lambda _user_id: team)
    [table] = client.get(
        f"/_api/sidecomps/{bear_pit.id}/standings", query_string={"viewer_player_id": team.id}
    ).get_json()["tables"]
    assert table["personal_row"] is None
    assert len(table["rows"]) == (16 if is_organizer else 3)


def test_incomplete_player_does_not_receive_a_personal_standing(client, bear_pit):
    login_as(client, db.session.get(Player, "incomplete-bear"))
    [table] = client.get(f"/_api/sidecomps/{bear_pit.id}/standings").get_json()["tables"]
    assert table["personal_row"] is None
    assert all(row["player_id"] != "incomplete-bear" for row in table["rows"])


@pytest.mark.parametrize("unlimited", [False, True])
def test_complete_bear_standings_rank_all_entries_within_the_selected_weapon(client, tournament, bear_pit, unlimited):
    if unlimited:
        bear_pit.only_show_top_n_results = None
        db.session.commit()
    else:
        _set_viewer(client, tournament, "tournament", "player-organizer")
    payload = client.get(f"/_api/sidecomps/{bear_pit.id}/standings", query_string={"weapon": "UNARMED"}).get_json()
    assert payload["showing_all_results"] is True
    [table] = payload["tables"]
    assert [row["player_id"] for row in table["rows"]] == ["UNARMED-leader", "UNARMED-second"]
    assert [row["rank"] for row in table["rows"]] == [1, 2]


def test_weapon_leader_uses_selected_sort_and_stable_tie_breaks(client, bear_pit):
    bear_pit.track_losses = True
    leader = SideCompRegistration.query.filter_by(comp=bear_pit.id, player="CHAIN-leader").one()
    second = SideCompRegistration.query.filter_by(comp=bear_pit.id, player="CHAIN-second").one()
    for _ in range(10):
        db.session.add(SideCompResult(comp=bear_pit.id, player=leader.id, points=-1, ref="ref"))
    for _ in range(4):
        db.session.add(SideCompResult(comp=bear_pit.id, player=second.id, points=1, ref="ref"))
    db.session.commit()
    path = f"/_api/sidecomps/{bear_pit.id}/standings"
    [by_percentage] = client.get(path, query_string={"weapon": "CHAIN"}).get_json()["tables"]
    assert by_percentage["rows"][0]["player_id"] == "CHAIN-second"
    [by_wins] = client.get(path, query_string={"weapon": "CHAIN", "sort": "wins"}).get_json()["tables"]
    assert by_wins["rows"][0]["player_id"] == "CHAIN-leader"

    _add_entry(bear_pit, "CHAIN-tied", Pompfen.CHAIN, 10, 10)
    [leader] = client.get(path, query_string={"weapon": "CHAIN", "sort": "wins"}).get_json()["tables"]
    assert leader["rows"][0]["player_id"] == "CHAIN-leader"
    [tied] = client.get(path, query_string={"sort": "wins"}).get_json()["tables"]
    assert [row["rank"] for row in tied["rows"]] == [1, 2, 3]
    assert [row["player_id"] for row in tied["rows"][:2]] == ["CHAIN-leader", "CHAIN-tied"]


def test_hidden_bear_results_include_neither_public_nor_personal_rows(client, bear_pit):
    bear_pit.only_show_top_n_results = 0
    db.session.commit()
    login_as(client, db.session.get(Player, "UNARMED-second"))
    [table] = client.get(f"/_api/sidecomps/{bear_pit.id}/standings").get_json()["tables"]
    assert table["rows"] == []
    assert table["personal_row"] is None
    assert "class_winners" not in table


def test_other_competitions_use_the_same_table_and_weapon_limit(client, bear_pit):
    bear_pit.type = "OTHER"
    db.session.commit()
    [table] = client.get(f"/_api/sidecomps/{bear_pit.id}/standings").get_json()["tables"]
    assert len(table["rows"]) == 3
    assert "class_winners" not in table
    [filtered] = client.get(f"/_api/sidecomps/{bear_pit.id}/standings", query_string={"weapon": "UNARMED"}).get_json()[
        "tables"
    ]
    assert [row["player_id"] for row in filtered["rows"]] == ["UNARMED-leader"]


def test_external_bear_summary_keeps_existing_schema_and_cutoff(client, tournament, bear_pit):
    token = issue_access_token(db.session.get(Player, "UNARMED-second"))
    response = client.get(
        f"/api/v1/tournaments/{tournament.url}/side-competitions/{bear_pit.id}/results",
        headers={"Authorization": f"Bearer {token}"},
    )
    [table] = response.get_json()["tables"]
    assert set(table) == {"id", "title", "track_losses", "rows"}
    assert [row["player"]["id"] for row in table["rows"]] == ["CHAIN-leader", "LONG-leader", "QTIP-leader"]


def test_default_service_summary_keeps_filter_before_limit_contract(chain_pit):
    [table] = SideCompService.standings(chain_pit.id, table_id="breaks", weapon="FLORENTINE").unwrap()["tables"]
    assert [row["player_id"] for row in table["rows"]] == ["florentine-0", "florentine-1", "florentine-2"]
    assert [row["rank"] for row in table["rows"]] == [1, 2, 3]
    assert table["personal_row"] is None
    assert "class_winners" not in table


@pytest.mark.parametrize("track_losses", [False, True])
def test_weapon_choices_require_valid_results_and_include_loss_only_entries(client, tournament, track_losses):
    comp = SideComp(
        event=tournament.url, name="Participation", type="DUELING", only_show_top_n_results=3, track_losses=track_losses
    )
    db.session.add(comp)
    db.session.commit()
    _add_entry(comp, "registered-only", Pompfen.CHAIN, 0)
    invalid = _add_entry(comp, "invalid-only", Pompfen.LONG, 0)
    db.session.add(SideCompResult(comp=comp.id, player=invalid.id, points=1, ref="ref", valid=False))
    loss = _add_entry(comp, "loss-only", Pompfen.QTIP, 0, 1)
    flagged = _add_entry(comp, "flagged-valid", Pompfen.STAFF, 1)
    SideCompResult.query.filter_by(player=flagged.id).one().flagged = True
    deleted = _add_entry(comp, "deleted-only", Pompfen.BOARD, 1)
    SideCompResult.query.filter_by(player=deleted.id).delete()
    _add_entry(comp, "incomplete-scored", None, 3)
    _add_entry(comp, "played", Pompfen.FLORENTINE, 2)
    db.session.commit()
    path = f"/_api/sidecomps/{comp.id}/standings"

    [table] = client.get(path).get_json()["tables"]
    assert table["available_weapons"] == ["QTIP", "STAFF", "FLORENTINE"]
    [loss_table] = client.get(path, query_string={"weapon": "QTIP"}).get_json()["tables"]
    assert loss_table["rows"][0]["player_id"] == "loss-only"
    assert loss_table["rows"][0]["wins"] == 0

    SideCompResult.query.filter_by(player=loss.id).one().valid = False
    db.session.commit()
    response = client.get(path, query_string={"weapon": "QTIP"})
    assert response.status_code == 200
    [invalidated] = response.get_json()["tables"]
    assert invalidated["available_weapons"] == ["STAFF", "FLORENTINE"]
    assert invalidated["rows"] == []


@pytest.mark.parametrize("is_organizer", [False, True])
@pytest.mark.parametrize("top_n", [None, 3])
def test_unplayed_weapons_are_not_selectable_but_complete_registrations_remain_in_all_results(
    client, tournament, is_organizer, top_n
):
    comp = SideComp(event=tournament.url, name="No scores", type="DUELING", only_show_top_n_results=top_n)
    db.session.add(comp)
    db.session.commit()
    _add_entry(comp, "unplayed-chain", Pompfen.CHAIN, 0)
    _add_entry(comp, "unplayed-long", Pompfen.LONG, 0)
    if is_organizer:
        _set_viewer(client, tournament, "tournament", "player-organizer")
    else:
        login_as(client, db.session.get(Player, "unplayed-chain"))
    path = f"/_api/sidecomps/{comp.id}/standings"
    [table] = client.get(path).get_json()["tables"]
    assert table["available_weapons"] == []
    assert [row["player_id"] for row in table["rows"]] == ["unplayed-chain", "unplayed-long"]
    if not is_organizer:
        assert table["personal_row"]["player_id"] == "unplayed-chain"
        assert table["personal_row"]["rank"] == 1

    response = client.get(path, query_string={"weapon": "LONG"})
    assert response.status_code == 200
    [filtered] = response.get_json()["tables"]
    assert filtered["available_weapons"] == []
    assert filtered["rows"] == []
    assert filtered["personal_row"] == table["personal_row"]


@pytest.mark.parametrize("comp_type", ["DUELING", "CHAIN_BREAKING", "OTHER"])
def test_competition_without_entries_has_empty_rows_and_weapon_choices(client, tournament, comp_type):
    comp = SideComp(event=tournament.url, name="Empty", type=comp_type, only_show_top_n_results=3)
    db.session.add(comp)
    db.session.commit()
    response = client.get(f"/_api/sidecomps/{comp.id}/standings")
    assert response.status_code == 200
    for table in response.get_json()["tables"]:
        assert table["rows"] == []
        assert table["available_weapons"] == []
        assert table["personal_row"] is None
        assert "class_winners" not in table
