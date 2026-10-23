"""Per-game box tables for teams and players, the layer every app reads.

    <comp>_<season>_team_games.csv     one row per team and game
    <comp>_<season>_player_games.csv   one row per player and game

Competitions with play-by-play get them from their events and stints (with seconds on court,
plus-minus and counted possessions); box-only competitions (FEB) write them directly from
their box scores. Both use the same columns, made / missed format:

    competition, season, game_id, date, round, team, team_name, crest_url, opponent, is_home,
    seconds, pts, fg2_made, fg2_missed, fg3_made, fg3_missed, ft_made, ft_missed,
    orb, drb, ast, stl, tov, blk, blk_against, pf, fouls_drawn, pir, possessions, level

player rows add player_id, player_name, number, photo_url, starter, plus_minus and possessions
(his team's offensive possessions while he was on court; opp_possessions, the rival's).
`level` is "pbp" (built from the play-by-play) or "box" (box score only).

    python -m warehouse.gametables            # every season built in data/warehouse
    python -m warehouse.gametables acb_2025   # one
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("data") / "warehouse"
STATS = ["pts", "fg2_made", "fg2_missed", "fg3_made", "fg3_missed", "ft_made", "ft_missed",
         "orb", "drb", "ast", "stl", "tov", "blk", "blk_against", "pf", "fouls_drawn"]
ID_COLS = ["competition", "season", "game_id", "date", "round", "team", "team_name", "crest_url",
           "opponent", "is_home"]
TEAM_COLUMNS = ID_COLS + ["seconds"] + STATS + ["pir", "possessions", "level"]
PLAYER_COLUMNS = ID_COLS + ["player_id", "player_name", "number", "photo_url", "starter", "seconds"] + STATS + \
    ["pir", "plus_minus", "possessions", "opp_possessions", "level"]
FOULS = {"foul", "foul_offensive", "foul_unsportsmanlike", "foul_technical"}
COUNTED = {"fg2_made", "fg2_missed", "fg3_made", "fg3_missed", "ft_made", "ft_missed", "orb", "drb",
           "ast", "stl", "tov", "blk", "blk_against"}


def pir(df: pd.DataFrame) -> pd.Series:
    """Performance index rating (valoración), the European efficiency."""
    return (df["pts"] + df["orb"] + df["drb"] + df["ast"] + df["stl"] + df["blk"] + df["fouls_drawn"]
            - df["fg2_missed"] - df["fg3_missed"] - df["ft_missed"] - df["tov"] - df["blk_against"] - df["pf"])


def _counts(ev: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    e = ev[ev["team"].notna()].copy()
    e["stat"] = e["action"].where(e["action"].isin(COUNTED))
    e.loc[e["action"].isin(FOULS), "stat"] = "pf"
    e.loc[e["action"] == "foul_drawn", "stat"] = "fouls_drawn"
    e.loc[e["action"] == "team_orb", "stat"] = "orb"
    e.loc[e["action"] == "team_drb", "stat"] = "drb"
    c = e.dropna(subset=["stat"]).groupby(keys + ["stat"]).size().unstack(fill_value=0)
    pts = e.groupby(keys)["points"].sum()
    c["pts"] = pts.reindex(c.index).fillna(0)
    for s in STATS:
        if s not in c:
            c[s] = 0
    return c[STATS].astype(int)


def from_events(events: pd.DataFrame, stints: pd.DataFrame, games: pd.DataFrame,
                people: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Team and player game rows of one competition-season with play-by-play."""
    events = events.copy()
    events["game_id"] = events["game_id"].astype(str)
    stints = stints.copy()
    stints["game_id"] = stints["game_id"].astype(str)
    g = games.copy()
    g["game_id"] = g["game_id"].astype(str)
    g = g.set_index("game_id")

    # ── team rows
    team = _counts(events, ["game_id", "team"]).reset_index()
    # team-only events (team rebounds, team turnovers…) are already in: they carry a team and no player
    length = events.groupby("game_id")["elapsed_s"].max()
    team["seconds"] = team["game_id"].map(length) * 5
    poss = events.dropna(subset=["possession_id"]).drop_duplicates(["game_id", "possession_id"]) \
        .groupby(["game_id", "offense"]).size()
    team["possessions"] = [poss.get((gid, t), np.nan) for gid, t in zip(team["game_id"], team["team"])]
    home = events[events["is_home"] == True].groupby("game_id")["team"].first()    # noqa: E712
    away = events[events["is_home"] == False].groupby("game_id")["team"].first()   # noqa: E712
    # only the two teams of the game (a stray code in the source, e.g. EuroCup 2017 game 3, is dropped)
    team = team[(team["team"] == team["game_id"].map(home)) | (team["team"] == team["game_id"].map(away))].copy()
    team["is_home"] = team["team"] == team["game_id"].map(home)
    team["opponent"] = np.where(team["is_home"], team["game_id"].map(away), team["game_id"].map(home))
    _game_info(team, g)

    # ── player rows
    pl = _counts(events[events["player_id"].notna()], ["game_id", "team", "player_id"]).reset_index()
    secs, pm, starters, own_poss, opp_poss = _on_court(stints, events)
    keys = list(zip(pl["game_id"], pl["player_id"].astype(str)))
    pl["seconds"] = [secs.get(k, 0.0) for k in keys]
    events["home_team"] = events["game_id"].map(home)
    own_poss, opp_poss = _possessions_on_court(events)
    pl["possessions"] = [own_poss.get(k, 0) for k in keys]          # his team's possessions while on court
    pl["opp_possessions"] = [opp_poss.get(k, 0) for k in keys]
    pl["plus_minus"] = [pm.get(k, 0) for k in keys]
    pl["starter"] = [k in starters for k in keys]
    # players who were on court without any event still played: add them
    for (gid, pid), s in secs.items():
        if (gid, pid) not in set(keys) and s > 0:
            side = _side_of(stints, gid, pid)
            if side is not None:
                row = {"game_id": gid, "team": side, "player_id": pid, "seconds": s, "plus_minus": pm.get((gid, pid), 0),
                       "possessions": own_poss.get((gid, pid), 0), "opp_possessions": opp_poss.get((gid, pid), 0),
                       "starter": (gid, pid) in starters, **{c: 0 for c in STATS}}
                pl = pd.concat([pl, pd.DataFrame([row])], ignore_index=True)
    names = events.dropna(subset=["player_id"]).groupby(["game_id", "player_id"])["player_name"].last()
    pl["player_name"] = [names.get((gid, pid)) for gid, pid in zip(pl["game_id"], pl["player_id"])]
    pl = pl[(pl["team"] == pl["game_id"].map(home)) | (pl["team"] == pl["game_id"].map(away))].copy()
    pl["is_home"] = pl["team"] == pl["game_id"].map(home)
    pl["opponent"] = np.where(pl["is_home"], pl["game_id"].map(away), pl["game_id"].map(home))
    _game_info(pl, g)
    if people is not None and not people.empty:
        p = people.drop_duplicates("player_id", keep="last").set_index("player_id")
        for c in ("number", "photo_url"):
            if c in p:
                pl[c] = pl["player_id"].astype(str).map(p[c])
    team["level"] = pl["level"] = "pbp"
    return _finish(team, TEAM_COLUMNS), _finish(pl, PLAYER_COLUMNS)


def _on_court(stints: pd.DataFrame, events: pd.DataFrame):
    secs, pm, starters, own_poss, opp_poss = {}, {}, set(), {}, {}
    for r in stints.itertuples():
        for side, own, other, po, pa in (("home_on", r.pts_home, r.pts_away, r.poss_home, r.poss_away),
                                          ("away_on", r.pts_away, r.pts_home, r.poss_away, r.poss_home)):
            for p in str(getattr(r, side)).split("|"):
                if p and p != "nan":
                    k = (r.game_id, p)
                    secs[k] = secs.get(k, 0.0) + float(r.seconds)
                    pm[k] = pm.get(k, 0) + int(own) - int(other)
                    own_poss[k] = own_poss.get(k, 0) + int(po)
                    opp_poss[k] = opp_poss.get(k, 0) + int(pa)
    first = stints.sort_values(["game_id", "start_s"]).groupby("game_id").first()
    for gid, r in first.iterrows():
        for side in ("home_on", "away_on"):
            starters |= {(gid, p) for p in str(r[side]).split("|") if p}
    return secs, pm, starters, own_poss, opp_poss


def _possessions_on_court(events: pd.DataFrame):
    """Each possession is credited to the ten players on court when it started."""
    ev = events.dropna(subset=["possession_id"])
    ev = ev[~ev["action"].isin(["sub_in", "sub_out"])]
    first = ev.drop_duplicates(["game_id", "possession_id"])
    own, opp = {}, {}
    for gid, off, is_home_off, h, a in zip(first["game_id"], first["offense"], first["offense"] == first["home_team"],
                                           first["home_on"], first["away_on"]):
        o_on, d_on = (h, a) if is_home_off else (a, h)
        for p in str(o_on).split("|"):
            if p and p != "nan":
                own[(gid, p)] = own.get((gid, p), 0) + 1
        for p in str(d_on).split("|"):
            if p and p != "nan":
                opp[(gid, p)] = opp.get((gid, p), 0) + 1
    return own, opp


def _side_of(stints: pd.DataFrame, gid: str, pid: str):
    s = stints[stints["game_id"] == gid]
    if s["home_on"].str.contains(pid, regex=False).any():
        return s["home"].iloc[0]
    if s["away_on"].str.contains(pid, regex=False).any():
        return s["away"].iloc[0]
    return None


def _game_info(df: pd.DataFrame, g: pd.DataFrame) -> None:
    for c in ("competition", "season", "date", "round"):
        df[c] = df["game_id"].map(g[c]) if c in g else None
    home_name = g["home_name"] if "home_name" in g else pd.Series(dtype=object)
    away_name = g["away_name"] if "away_name" in g else pd.Series(dtype=object)
    home_crest = g["home_crest"] if "home_crest" in g else pd.Series(dtype=object)
    away_crest = g["away_crest"] if "away_crest" in g else pd.Series(dtype=object)
    df["team_name"] = np.where(df["is_home"], df["game_id"].map(home_name), df["game_id"].map(away_name))
    df["crest_url"] = np.where(df["is_home"], df["game_id"].map(home_crest), df["game_id"].map(away_crest))
    df["team_name"] = df["team_name"].where(pd.notna(df["team_name"]), df["team"])


def _finish(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for c in columns:
        if c not in df:
            df[c] = None
    for c in STATS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    df["pir"] = pd.to_numeric(df["pir"], errors="coerce").fillna(pir(df))     # keep an official value if given
    return df[columns].sort_values(["game_id", "team"] + (["player_id"] if "player_id" in columns else [])) \
        .reset_index(drop=True)


def from_box(team_rows: pd.DataFrame, player_rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Box-only sources: rows already in the standard columns (missing ones are filled)."""
    team_rows, player_rows = team_rows.copy(), player_rows.copy()
    team_rows["level"] = player_rows["level"] = "box"
    if "possessions" not in team_rows or team_rows["possessions"].isna().all():
        fga = team_rows["fg2_made"] + team_rows["fg2_missed"] + team_rows["fg3_made"] + team_rows["fg3_missed"]
        fta = team_rows["ft_made"] + team_rows["ft_missed"]
        team_rows["possessions"] = (fga + 0.44 * fta - team_rows["orb"] + team_rows["tov"]).round(1)
    return _finish(team_rows, TEAM_COLUMNS), _finish(player_rows, PLAYER_COLUMNS)


def build(tag: str, out: Path = OUT) -> tuple[int, int]:
    """Writes <tag>_team_games.csv and <tag>_player_games.csv from a built season."""
    ev = pd.read_csv(out / f"{tag}_events.csv.gz", dtype={"player_id": str, "game_id": str}, low_memory=False)
    st = pd.read_csv(out / f"{tag}_stints.csv.gz", dtype={"game_id": str, "home_on": str, "away_on": str})
    games = pd.read_csv(out / f"{tag}_games.csv", dtype={"game_id": str})
    people_path = out / f"{tag}_people.csv"
    people = pd.read_csv(people_path, dtype={"player_id": str}) if people_path.exists() else None
    if "lineups_ok" in games:        # minutes and plus-minus only where lineups are reliable
        bad = set(games.loc[~games["lineups_ok"].astype(bool), "game_id"])
    else:
        bad = set()
    team, players = from_events(ev, st, games, people)
    players.loc[players["game_id"].isin(bad), ["plus_minus", "possessions", "opp_possessions"]] = np.nan
    team.to_csv(out / f"{tag}_team_games.csv", index=False)
    players.to_csv(out / f"{tag}_player_games.csv", index=False)
    return len(team), len(players)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tags", nargs="*", help="e.g. acb_2025 euroleague_2024 (default: every built season)")
    ap.add_argument("--force", action="store_true", help="Rebuild even if the tables are up to date.")
    args = ap.parse_args()
    tags = args.tags or sorted(p.name[:-len("_events.csv.gz")] for p in OUT.glob("*_events.csv.gz"))
    for tag in tags:
        src, dst = OUT / f"{tag}_events.csv.gz", OUT / f"{tag}_player_games.csv"
        if not args.force and dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            continue
        n_t, n_p = build(tag)
        print(f"{tag}: {n_t} team rows, {n_p} player rows")


def load(tag: str, out: Path = OUT) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(team_games, player_games) of one competition-season."""
    t = pd.read_csv(out / f"{tag}_team_games.csv", dtype={"game_id": str})
    p = pd.read_csv(out / f"{tag}_player_games.csv", dtype={"game_id": str, "player_id": str})
    return t, p


def literal(v):
    try:
        return ast.literal_eval(v) if isinstance(v, str) else v
    except (ValueError, SyntaxError):
        return v


if __name__ == "__main__":
    main()
