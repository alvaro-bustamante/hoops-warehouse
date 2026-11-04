"""Unit tests of the warehouse core on a small synthetic game (no downloads needed).

    python -m pytest -q
"""
import pandas as pd
import pytest

from warehouse import context
from warehouse.pipeline import build_game, player_seconds
from warehouse.possessions import attach_possessions, possession_table
from warehouse.schema import box_from_events, elapsed, finish

HOME = [f"H{i}" for i in range(1, 6)]
AWAY = [f"A{i}" for i in range(1, 6)]


def ev(t, team, player, action, home=None, away=None):
    return {"source": "test", "competition": "euroleague", "season": 2025, "game_id": "1", "period": 1,
            "clock_s": 600 - t, "elapsed_s": float(t), "team": team,
            "is_home": None if team is None else team == "HOM", "player_id": player, "player_name": player,
            "action": action, "score_home": home, "score_away": away}


@pytest.fixture
def game() -> pd.DataFrame:
    rows = [
        ev(0, None, None, "period_start"),
        ev(0, "HOM", "H1", "jump_ball"), ev(0, "AWY", "A1", "jump_ball"),
        ev(10, "HOM", "H2", "fg2_made", 2, 0), ev(10, "HOM", "H3", "ast"),           # possession 1 (home)
        ev(20, "AWY", "A2", "fg3_missed"), ev(21, "HOM", "H4", "drb"),                # 2 (away) -> 3 (home)
        ev(30, "HOM", "H5", "fg3_made", 5, 0),
        ev(40, "AWY", "A3", "fg2_missed"), ev(41, "AWY", "A4", "orb"),               # 4 (away), kept by the ORB
        ev(45, "AWY", "A5", "fg2_made", 5, 2),
        ev(50, "HOM", "H2", "fg2_made", 7, 2), ev(50, "AWY", "A2", "foul"),          # 5 (home): and-one
        ev(50, "HOM", "H2", "ft_made", 8, 2),
        ev(100, "AWY", "A1", "sub_out"), ev(100, "AWY", "A6", "sub_in"),
        ev(120, "AWY", "A6", "fg2_made", 8, 4),                                       # 6 (away)
        ev(300, "HOM", "H1", "tov"),                                                  # 7 (home)
        ev(600, None, None, "period_end"), ev(600, None, None, "game_end"),
    ]
    return finish(rows)


def test_elapsed_time():
    assert elapsed(1, 600) == 0
    assert elapsed(4, 0) == 2400
    assert elapsed(5, 300) == 2400            # start of the first overtime
    assert elapsed(4, 0, regular=720) == 2880  # NBA quarters


def test_finish_scores_and_points(game):
    assert list(game["event_num"]) == list(range(1, len(game) + 1))
    assert game["points"].sum() == 12
    assert tuple(game[["score_home", "score_away"]].iloc[-1]) == (8, 4)


def test_box_from_events(game):
    box = box_from_events(game).set_index("team")
    assert box.loc["HOM", "pts"] == 8 and box.loc["AWY", "pts"] == 4
    assert box.loc["HOM", "fg2_made"] == 2 and box.loc["HOM", "fg3_made"] == 1 and box.loc["HOM", "ft_made"] == 1
    assert box.loc["AWY", "orb"] == 1 and box.loc["HOM", "drb"] == 1
    players = box_from_events(game, by="player").set_index(["team", "player_id"])
    assert players.loc[("HOM", "H2"), "pts"] == 5


def test_possessions_offensive_rebound_and_and_one(game):
    poss = possession_table(attach_possessions(game))
    assert poss.groupby("offense").size().to_dict() == {"AWY": 3, "HOM": 4}
    assert list(poss["offense"]) == ["HOM", "AWY", "HOM", "AWY", "HOM", "AWY", "HOM"]
    # the offensive rebound keeps the possession, which ends in a basket
    p4 = poss.iloc[3]
    assert p4["orb"] == 1 and p4["points"] == 2
    # the and-one free throw stays in the possession of the made shot
    assert poss.iloc[4]["points"] == 3
    assert poss["points"].sum() == 12


def test_lineups_stints_and_minutes(game):
    res = build_game(game)
    st = res["stints"]
    assert len(st) == 2
    assert st["seconds"].sum() == pytest.approx(600)
    assert set(st.iloc[0]["away_on"].split("|")) == set(AWAY)
    assert set(st.iloc[1]["away_on"].split("|")) == set(AWAY[1:] + ["A6"])
    secs = player_seconds(st)
    assert secs["A1"] == pytest.approx(100) and secs["A6"] == pytest.approx(500)
    assert all(secs[p] == pytest.approx(600) for p in HOME)
    assert res["report"]["lineups_ok"] and res["report"]["score_ok"]


def test_build_game_against_an_official_box(game):
    box = box_from_events(game, by="player")
    secs = player_seconds(build_game(game)["stints"])
    official = box.assign(seconds=box["player_id"].map(secs).fillna(0))
    rep = build_game(game, official)["report"]
    assert rep["box_mismatches"] == 0
    assert rep["minutes_max_gap_s"] == pytest.approx(0)
    assert rep["possessions"] == {"HOM": 4, "AWY": 3}
    # a wrong official sheet is caught
    bad = official.copy()
    bad.loc[bad["player_id"] == "H2", "pts"] += 1
    assert build_game(game, bad)["report"]["box_mismatches"] == 1


def test_travel_distance_and_time_zones():
    # Madrid -> Barcelona, about 505 km
    assert context._km(40.4168, -3.7038, 41.3874, 2.1686) == pytest.approx(505, abs=10)
    assert pd.isna(context._km(None, 0, 0, 0))
    assert context._tz("America/Chicago", "America/New_York") == "America/Chicago"
    assert context._tz("Eastern", "America/New_York") == "America/New_York"      # not an IANA name
    winter = context._offset("America/New_York", pd.Timestamp("2025-01-15"))
    summer = context._offset("America/New_York", pd.Timestamp("2025-07-15"))
    assert (winter, summer) == (-5, -4)
