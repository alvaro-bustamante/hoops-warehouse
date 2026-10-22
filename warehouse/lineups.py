"""Who is on court at every event, and lineup stints.

The five players of each team at the start of every period are inferred from
the events: a player whose first appearance in the period is anything other
than entering the game (a shot, a rebound, being substituted out…) started the
period. Players who start a period without doing anything are carried over from
the end of the previous period.
"""
from __future__ import annotations

import pandas as pd

ON_COURT_ACTIONS = {"fg2_made", "fg2_missed", "fg3_made", "fg3_missed", "ft_made", "ft_missed", "orb", "drb",
                    "ast", "tov", "stl", "blk", "blk_against", "foul", "foul_offensive", "foul_unsportsmanlike",
                    "foul_drawn", "jump_ball"}


def _period_starters(ev: pd.DataFrame, team: str, previous_end: list[str]) -> tuple[list[str], str | None]:
    first: dict[str, str] = {}
    for pid, action in zip(ev["player_id"], ev["action"]):
        if pid is None or pd.isna(pid) or pid in first:
            continue
        if action == "sub_in" or action == "sub_out" or action in ON_COURT_ACTIONS:
            first[pid] = action
    starters = [p for p, a in first.items() if a != "sub_in"]
    opening = ev[(ev["action"] == "sub_in") & (ev["clock_s"] >= ev["clock_s"].max())]["player_id"].tolist() \
        if len(ev) else []                       # e.g. ACB lists the starting five as entries
    note = None
    if len(set(opening)) == 5:          # the feed lists the starting five explicitly: trust it
        return [], None                 # (they enter through their own sub_in events)
    if len(starters) < 5:                       # silent starters: carried over from the previous period
        silent = [p for p in previous_end if p not in first]
        starters += silent[:5 - len(starters)]
    if len(starters) + len([p for p in opening if p not in starters]) != 5 and len(starters) != 5:
        note = f"{team}: {len(starters)} starters inferred"
    if len(starters) > 5:
        # a substitution is missing from the feed: keep the most certain five. First, players whose
        # first move is leaving the court; then those on court at the end of the previous period;
        # then the earliest to appear.
        rank = {p: (0 if first[p] == "sub_out" else 1 if p in previous_end else 2, i)
                for i, p in enumerate(starters)}
        starters = sorted(starters, key=lambda p: rank[p])[:5]
        note += " (kept the 5 most likely)"
    return starters[:5], note


def attach_lineups(events: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Adds home_on / away_on ('id|id|id|id|id', sorted) to every event of ONE game."""
    ev = events.copy()
    home = ev.loc[ev["is_home"] == True, "team"].dropna().iloc[0]   # noqa: E712
    away = ev.loc[ev["is_home"] == False, "team"].dropna().iloc[0]  # noqa: E712
    notes, end_of = [], {home: [], away: []}
    home_on, away_on = [], []
    for period, block in ev.groupby("period", sort=True):
        on = {}
        for team in (home, away):
            mask = (block["team"] == team) & block["player_id"].notna()
            starters, note = _period_starters(block[mask], team, end_of[team])
            if note:
                notes.append(f"period {period}: {note}")
            on[team] = set(starters)
        for team, pid, action in zip(block["team"], block["player_id"], block["action"]):
            if action == "sub_out" and team in on:
                on[team].discard(pid)
            elif action == "sub_in" and team in on:
                on[team].add(pid)
            home_on.append("|".join(sorted(on[home])))
            away_on.append("|".join(sorted(on[away])))
        end_of = {t: sorted(on[t]) for t in (home, away)}
    ev["home_on"], ev["away_on"] = home_on, away_on
    sizes = ev[~ev["action"].isin(["sub_in", "sub_out"])]
    bad = ((sizes["home_on"].str.count(r"\|") != 4) | (sizes["away_on"].str.count(r"\|") != 4)).sum()
    if bad:
        notes.append(f"{bad} events without exactly five players per team")
    return ev, notes


def stints(events: pd.DataFrame) -> pd.DataFrame:
    """One row per uninterrupted pair of lineups (needs attach_lineups and possessions)."""
    change = events["action"].isin(["sub_in", "sub_out", "period_start"])
    since = events["elapsed_s"].where(change).ffill().fillna(0.0)   # time of the last lineup change
    ev = events.assign(_since=since)[~events["action"].isin(["sub_in", "sub_out"])].copy()
    key = ev["home_on"] + "/" + ev["away_on"] + "/" + ev["period"].astype(str)
    ev["stint"] = (key != key.shift()).cumsum()
    home_team = ev.loc[ev["is_home"] == True, "team"].dropna().iloc[0]   # noqa: E712
    away_team = ev.loc[ev["is_home"] == False, "team"].dropna().iloc[0]  # noqa: E712
    rows = []
    for sid, g in ev.groupby("stint"):
        pts = g.groupby("team")["points"].sum()
        poss = g.dropna(subset=["possession_id"]).drop_duplicates("possession_id").groupby("offense").size() \
            if "possession_id" in g else pd.Series(dtype=int)
        rows.append({
            "competition": g["competition"].iloc[0], "season": g["season"].iloc[0], "game_id": g["game_id"].iloc[0],
            "stint": int(sid), "period": int(g["period"].iloc[0]),
            "start_s": float(g["_since"].iloc[0]), "end_s": float(g["elapsed_s"].iloc[-1]),
            "home": home_team, "away": away_team, "home_on": g["home_on"].iloc[0], "away_on": g["away_on"].iloc[0],
            "pts_home": int(pts.get(home_team, 0)), "pts_away": int(pts.get(away_team, 0)),
            "poss_home": int(poss.get(home_team, 0)), "poss_away": int(poss.get(away_team, 0)),
        })
    out = pd.DataFrame(rows)
    # a stint lasts until the next one starts
    out["end_s"] = out["start_s"].shift(-1).fillna(out["end_s"])
    out["seconds"] = out["end_s"] - out["start_s"]
    return out
