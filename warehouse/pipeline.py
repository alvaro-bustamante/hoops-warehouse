"""From one game's events to the warehouse tables, with checks against the official box score."""
from __future__ import annotations

import pandas as pd

from .lineups import attach_lineups, stints
from .possessions import attach_possessions, formula_possessions, possession_table
from .schema import box_from_events

STATS = ["fg2_made", "fg2_missed", "fg3_made", "fg3_missed", "ft_made", "ft_missed",
         "orb", "drb", "ast", "tov", "stl", "blk", "pts"]


def player_seconds(st: pd.DataFrame) -> pd.Series:
    """Seconds on court per player id, from the stints."""
    rows = []
    for side in ("home_on", "away_on"):
        for ids, secs in zip(st[side], st["seconds"]):
            rows += [(p, secs) for p in ids.split("|") if p]
    return pd.DataFrame(rows, columns=["player_id", "s"]).groupby("player_id")["s"].sum()


def build_game(events: pd.DataFrame, official_players: pd.DataFrame | None = None) -> dict:
    ev, notes = attach_lineups(events)
    ev = attach_possessions(ev)
    st = stints(ev)
    poss = possession_table(ev)
    report = {"game_id": ev["game_id"].iloc[0], "events": len(ev), "notes": notes}

    final = ev[["score_home", "score_away"]].iloc[-1]
    box = box_from_events(ev)
    home = ev.loc[ev["is_home"] == True, "team"].dropna().iloc[0]   # noqa: E712
    away = ev.loc[ev["is_home"] == False, "team"].dropna().iloc[0]  # noqa: E712
    pts = box.set_index("team")["pts"]
    report["score_ok"] = int(pts.get(home, 0)) == final.score_home and int(pts.get(away, 0)) == final.score_away

    if official_players is not None and not official_players.empty:
        mine = box_from_events(ev, by="player").set_index(["team", "player_id"])
        off = official_players.set_index(["team", "player_id"])
        diff = (mine.reindex(off.index).fillna(0)[STATS] - off[STATS]).abs()
        report["box_mismatches"] = int(diff.to_numpy().sum())
        if "seconds" in off.columns:
            secs = player_seconds(st)
            gap = (secs.reindex(off.index.get_level_values("player_id")).fillna(0).to_numpy()
                   - off["seconds"].to_numpy())
            report["minutes_max_gap_s"] = float(abs(gap).max()) if len(gap) else 0.0
            # the official sheet itself can be wrong (e.g. 40 minutes credited to a player who did not play):
            # its team totals must add up to 5 players x game length
            expected = 5 * float(ev["elapsed_s"].max())
            totals = off.groupby(level="team")["seconds"].sum()
            report["official_minutes_ok"] = bool((totals - expected).abs().max() <= 60)

    counted = poss.groupby("offense").size()
    estimate = formula_possessions(box.set_index("team"))
    report["possessions"] = {t: int(counted.get(t, 0)) for t in (home, away)}
    report["possessions_formula"] = {t: round(float(estimate.get(t, 0)), 1) for t in (home, away)}
    return {"events": ev, "stints": st, "possessions": poss, "report": report}
