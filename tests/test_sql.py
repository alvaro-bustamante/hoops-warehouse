"""The DuckDB layer on a tiny warehouse written to a temporary folder."""
import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from warehouse import sql  # noqa: E402


@pytest.fixture
def base(tmp_path):
    pd.DataFrame({"competition": ["nba"], "season": [2024], "game_id": ["0022400001"], "home": ["LAL"],
                  "away": ["BOS"], "score_ok": [True], "box_mismatches": [0], "minutes_max_gap_s": [0.4],
                  "lineups_ok": [True]}).to_csv(tmp_path / "nba_2024_games.csv", index=False)
    pd.DataFrame({"competition": ["euroleague"], "season": [2025], "game_id": ["12"], "home": ["MAD"],
                  "away": ["BAR"], "score_ok": [False], "box_mismatches": [2], "minutes_max_gap_s": [3.0]}) \
        .to_csv(tmp_path / "euroleague_2025_games.csv", index=False)
    tg = pd.DataFrame({"game_id": ["0022400001"] * 2, "team": ["LAL", "BOS"], "opponent": ["BOS", "LAL"],
                       "is_home": [True, False], "pts": [110, 100], "possessions": [100.0, 100.0]})
    tg.to_csv(tmp_path / "nba_2024_team_games.csv", index=False)
    pd.DataFrame({"player_id": ["2544"], "birthdate": ["1984-12-30"]}).to_csv(tmp_path / "nba_players.csv",
                                                                              index=False)
    return tmp_path


def test_views_and_names(base):
    con = sql.connect(base)
    names = dict(sql.tables(con))
    assert names == {"games": 2, "team_games": 2, "players": 1}      # team_games not swallowed by games
    g = con.sql("SELECT competition, season, game_id FROM games ORDER BY competition").df()
    assert list(g["competition"]) == ["euroleague", "nba"]
    assert list(g["season"]) == [2025, 2024]
    assert "0022400001" in set(g["game_id"])                          # leading zeros kept
    # a column missing in one league is NULL there, not an error
    assert con.sql("SELECT count(*) FROM games WHERE lineups_ok IS NULL").fetchall()[0][0] == 1
    assert con.sql("SELECT competition FROM players").fetchall()[0][0] == "nba"


def test_example_query_runs(base):
    con = sql.connect(base)
    q = (sql.Path(__file__).resolve().parents[1] / "queries" / "data_quality.sql").read_text(encoding="utf-8")
    out = con.sql(q).df().set_index("competition")
    assert out.loc["nba", "pct_exact_box"] == 100 and out.loc["euroleague", "pct_right_score"] == 0
    assert pd.isna(out.loc["euroleague", "pct_lineups_ok"])          # no such check there: no number, not 0


def test_parquet_copy(base, tmp_path):
    con = sql.connect(base)
    sql.to_parquet(con, tmp_path / "pq")
    back = duckdb.sql(f"SELECT * FROM read_parquet('{(tmp_path / 'pq' / 'games').as_posix()}/**/*.parquet', "
                      "hive_partitioning = true)").df()
    assert len(back) == 2
