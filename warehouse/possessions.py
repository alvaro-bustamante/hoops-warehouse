"""Possessions from the event sequence.

A possession belongs to the team with the ball. It changes when the other team
does something only the team with the ball can do (shoot, get a defensive
rebound, turn the ball over…). Offensive rebounds keep the same possession,
and an and-one free throw stays in the possession of the made shot.
Free throws for technical fouls do not change who has the ball.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

OWNER_ACTIONS = {"fg2_made", "fg2_missed", "fg3_made", "fg3_missed", "ft_made", "ft_missed", "orb", "drb",
                 "team_orb", "team_drb", "tov", "foul_offensive"}
TECHNICALS = {"foul_technical"}


def attach_possessions(events: pd.DataFrame) -> pd.DataFrame:
    """Adds possession_id (per game) and offense (team with the ball) to ONE game's events."""
    ev = events.copy()
    teams = ev["team"].dropna().unique().tolist()
    pid, owner = 0, None
    ids, offense = [], []
    tech_at: dict[float, set] = {}
    for t, a, s in zip(ev["team"], ev["action"], ev["elapsed_s"]):
        if a in TECHNICALS and t is not None:
            tech_at.setdefault(s, set()).add(t)
    for t, a, s in zip(ev["team"], ev["action"], ev["elapsed_s"]):
        if a == "period_start":
            owner = None
        elif a in OWNER_ACTIONS and isinstance(t, str):
            technical_ft = a.startswith("ft") and owner is not None and t != owner and \
                any(o != t for o in tech_at.get(s, set()))
            if not technical_ft and t != owner:
                pid += 1
                owner = t
        ids.append(pid if owner is not None else np.nan)
        offense.append(owner)
    ev["possession_id"], ev["offense"] = ids, offense
    ev.loc[ev["offense"].isna(), "possession_id"] = np.nan
    ev.attrs["teams"] = teams
    return ev


def possession_table(events: pd.DataFrame) -> pd.DataFrame:
    """One row per possession: team, start/end time, points, how it ended."""
    ev = events.dropna(subset=["possession_id"])
    rows = []
    for pid, g in ev.groupby("possession_id"):
        off = g["offense"].iloc[0]
        own = g[g["team"] == off]
        last = own[own["action"].isin(OWNER_ACTIONS)]
        rows.append({"competition": g["competition"].iloc[0], "season": g["season"].iloc[0],
                     "game_id": g["game_id"].iloc[0], "possession_id": int(pid), "period": int(g["period"].iloc[0]),
                     "offense": off, "start_s": float(g["elapsed_s"].iloc[0]), "end_s": float(g["elapsed_s"].iloc[-1]),
                     "points": int(own["points"].sum()),
                     "orb": int(own["action"].isin(["orb", "team_orb"]).sum()),
                     "end": last["action"].iloc[-1] if len(last) else "other",
                     "home_on": g["home_on"].iloc[0] if "home_on" in g else None,
                     "away_on": g["away_on"].iloc[0] if "away_on" in g else None})
    out = pd.DataFrame(rows)
    if len(out):   # a possession starts when the previous one ends (or when the period starts)
        period_start = out["period"].map(lambda q: 600.0 * (q - 1) if q <= 4 else 2400.0 + 300.0 * (q - 5))
        if (events["competition"] == "nba").any():
            period_start = out["period"].map(lambda q: 720.0 * (q - 1) if q <= 4 else 2880.0 + 300.0 * (q - 5))
        prev_end = out.groupby("period")["end_s"].shift(1)
        out["start_s"] = prev_end.fillna(period_start)
    return out


def formula_possessions(box: pd.DataFrame) -> pd.Series:
    """Classic estimate: FGA + 0.44·FTA − ORB + TOV (with made/missed columns)."""
    fga = box.fg2_made + box.fg2_missed + box.fg3_made + box.fg3_missed
    fta = box.ft_made + box.ft_missed
    return fga + 0.44 * fta - box.orb + box.tov
