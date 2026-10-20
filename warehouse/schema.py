"""Common event schema shared by every source.

One row per event. Actions are mutually exclusive: a shot is either made or
missed, never "attempted" (attempts are derived when a metric needs them).
"""
from __future__ import annotations

import pandas as pd

# Canonical actions ---------------------------------------------------------------
SHOTS = {"fg2_made": (2, True), "fg2_missed": (2, False), "fg3_made": (3, True), "fg3_missed": (3, False),
         "ft_made": (1, True), "ft_missed": (1, False)}
ACTIONS = [
    *SHOTS,
    "orb", "drb",                    # player rebounds
    "team_orb", "team_drb",          # team rebounds (no player)
    "ast", "tov", "stl", "blk", "blk_against",
    "foul", "foul_offensive", "foul_technical", "foul_unsportsmanlike", "foul_drawn",
    "sub_in", "sub_out",
    "jump_ball", "timeout", "period_start", "period_end", "game_end", "other",
]

COLUMNS = [
    "source", "competition", "season", "game_id",
    "event_num",        # order within the game (as given by the source)
    "period",           # 1-4, 5+ = overtimes
    "clock_s",          # seconds left in the period
    "elapsed_s",        # seconds since tip-off
    "team",             # team code of the actor (None for game events)
    "is_home",          # actor is the home team
    "player_id", "player_name",
    "action",           # canonical action (ACTIONS)
    "points",           # points scored by this event
    "score_home", "score_away",   # score after the event (forward-filled)
    "x", "y",           # shot location as given by the source (source units)
    "fastbreak", "second_chance",
    "raw_type", "raw_text",
]

PERIOD_SECONDS = {"euroleague": (600, 300), "eurocup": (600, 300), "acb": (600, 300), "nba": (720, 300)}


def elapsed(period: int, clock_s: float, regular: int = 600, overtime: int = 300) -> float:
    if period <= 4:
        return (period - 1) * regular + (regular - clock_s)
    return 4 * regular + (period - 5) * overtime + (overtime - clock_s)


def finish(rows: list[dict]) -> pd.DataFrame:
    """List of event dicts -> DataFrame with every column, scores forward-filled."""
    df = pd.DataFrame(rows)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[COLUMNS].copy()
    unknown = set(df["action"]) - set(ACTIONS)
    if unknown:
        raise ValueError(f"Unknown actions: {unknown}")
    df["points"] = df["action"].map(lambda a: SHOTS[a][0] if a in SHOTS and SHOTS[a][1] else 0)
    for c in ("score_home", "score_away"):
        df[c] = pd.to_numeric(df[c], errors="coerce").ffill().fillna(0).astype(int)
    df["event_num"] = range(1, len(df) + 1)
    return df


def box_from_events(events: pd.DataFrame, by: str = "team") -> pd.DataFrame:
    """Box-score totals rebuilt from the events (made / missed format)."""
    keys = [by] if by == "team" else ["team", "player_id"]
    ev = events[events["team"].notna()]
    counts = ev.groupby(keys + ["action"]).size().unstack(fill_value=0)
    out = pd.DataFrame(index=counts.index)
    for a in ["fg2_made", "fg2_missed", "fg3_made", "fg3_missed", "ft_made", "ft_missed",
              "orb", "drb", "team_orb", "team_drb", "ast", "tov", "stl", "blk", "blk_against"]:
        out[a] = counts[a] if a in counts else 0
    out["pts"] = 2 * out.fg2_made + 3 * out.fg3_made + out.ft_made
    if by == "team":                     # official team totals include team rebounds
        out["orb"] += out.pop("team_orb")
        out["drb"] += out.pop("team_drb")
    return out.reset_index()
