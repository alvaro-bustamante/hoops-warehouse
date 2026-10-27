"""Game context of every team in every game: rest, travel, time zones, crowd, TV.

    python -m warehouse.context wnba_2025          # writes data/warehouse/wnba_2025_context.csv
    python -m warehouse.context --all wnba         # every built WNBA season

One row per team and game:
  rest_days        full days without a game since the team's previous game (0 = back-to-back)
  games_last_7d    games played in the 7 days before this one
  travel_km        great-circle distance from the venue of the team's previous game
  tz_shift_h       change of UTC offset since the previous game (+ = travelled east)
  direction        "east", "west" or "none" (same offset)
  trip_game        n-th consecutive away game (0 at home); stand_game: n-th consecutive home game
  attendance       official attendance; attendance_rel: attendance / the home team's highest home
                   attendance that season (a fill-rate proxy when the capacity is not known)
  no_crowd / neutral   games without fans or on a neutral court (e.g. the 2020 WNBA and NBA bubbles)
  national_tv      broadcast on national TV (when the source gives it)

Venues are city-level coordinates (enough for travel distance and time zones), with the moves of
each franchise and the bubble seasons. Edit VENUES / OVERRIDES below if a team plays elsewhere.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

OUT = Path("data") / "warehouse"

# team code -> list of (first season, last season, city, lat, lon, time zone)
VENUES = {
    "wnba": {
        "ATL": [(1997, 2100, "Atlanta", 33.749, -84.388, "America/New_York")],
        "CHI": [(1997, 2100, "Chicago", 41.878, -87.630, "America/Chicago")],
        "CON": [(1997, 2100, "Uncasville", 41.476, -72.090, "America/New_York")],
        "TUL": [(2010, 2015, "Tulsa", 36.154, -95.993, "America/Chicago")],
        "DAL": [(2016, 2100, "Arlington", 32.736, -97.108, "America/Chicago")],
        "IND": [(1997, 2100, "Indianapolis", 39.768, -86.158, "America/Indiana/Indianapolis")],
        "SAN": [(2003, 2017, "San Antonio", 29.424, -98.494, "America/Chicago")],
        "SAS": [(2003, 2017, "San Antonio", 29.424, -98.494, "America/Chicago")],
        "LVA": [(2018, 2100, "Las Vegas", 36.170, -115.140, "America/Los_Angeles")],
        "LAS": [(1997, 2100, "Los Angeles", 34.043, -118.267, "America/Los_Angeles")],
        "MIN": [(1997, 2100, "Minneapolis", 44.978, -93.265, "America/Chicago")],
        "NYL": [(1997, 2017, "New York", 40.751, -73.993, "America/New_York"),
                (2018, 2019, "White Plains", 41.034, -73.763, "America/New_York"),
                (2020, 2100, "Brooklyn", 40.683, -73.976, "America/New_York")],
        "PHO": [(1997, 2100, "Phoenix", 33.446, -112.071, "America/Phoenix")],
        "PHX": [(1997, 2100, "Phoenix", 33.446, -112.071, "America/Phoenix")],
        "SEA": [(1997, 2100, "Seattle", 47.622, -122.354, "America/Los_Angeles")],
        "WAS": [(1997, 2018, "Washington", 38.898, -77.021, "America/New_York"),
                (2019, 2100, "Washington SE", 38.845, -76.995, "America/New_York")],
        "GSV": [(2025, 2100, "San Francisco", 37.768, -122.388, "America/Los_Angeles")],
        "TOR": [(2026, 2100, "Toronto", 43.643, -79.379, "America/Toronto")],
        "POR": [(2026, 2100, "Portland", 45.532, -122.667, "America/Los_Angeles")],
    },
    "nba": {
        "ATL": [(1968, 2100, "Atlanta", 33.757, -84.396, "America/New_York")],
        "BOS": [(1946, 2100, "Boston", 42.366, -71.062, "America/New_York")],
        "BKN": [(2012, 2100, "Brooklyn", 40.683, -73.976, "America/New_York")],
        "CHA": [(2004, 2100, "Charlotte", 35.225, -80.839, "America/New_York")],
        "CHI": [(1966, 2100, "Chicago", 41.881, -87.674, "America/Chicago")],
        "CLE": [(1970, 2100, "Cleveland", 41.497, -81.688, "America/New_York")],
        "DAL": [(1980, 2100, "Dallas", 32.790, -96.810, "America/Chicago")],
        "DEN": [(1976, 2100, "Denver", 39.749, -105.008, "America/Denver")],
        "DET": [(1957, 2100, "Detroit", 42.341, -83.055, "America/Detroit")],
        "GSW": [(1971, 2018, "Oakland", 37.750, -122.203, "America/Los_Angeles"),
                (2019, 2100, "San Francisco", 37.768, -122.388, "America/Los_Angeles")],
        "HOU": [(1971, 2100, "Houston", 29.751, -95.362, "America/Chicago")],
        "IND": [(1976, 2100, "Indianapolis", 39.764, -86.155, "America/Indiana/Indianapolis")],
        "LAC": [(1984, 2023, "Los Angeles", 34.043, -118.267, "America/Los_Angeles"),
                (2024, 2100, "Inglewood", 33.945, -118.343, "America/Los_Angeles")],
        "LAL": [(1960, 2100, "Los Angeles", 34.043, -118.267, "America/Los_Angeles")],
        "MEM": [(2001, 2100, "Memphis", 35.138, -90.051, "America/Chicago")],
        "MIA": [(1988, 2100, "Miami", 25.781, -80.188, "America/New_York")],
        "MIL": [(1968, 2100, "Milwaukee", 43.045, -87.917, "America/Chicago")],
        "MIN": [(1989, 2100, "Minneapolis", 44.980, -93.276, "America/Chicago")],
        "NOP": [(2002, 2100, "New Orleans", 29.949, -90.082, "America/Chicago")],
        "NYK": [(1946, 2100, "New York", 40.751, -73.993, "America/New_York")],
        "OKC": [(2008, 2100, "Oklahoma City", 35.463, -97.515, "America/Chicago")],
        "ORL": [(1989, 2100, "Orlando", 28.539, -81.384, "America/New_York")],
        "PHI": [(1963, 2100, "Philadelphia", 39.901, -75.172, "America/New_York")],
        "PHX": [(1968, 2100, "Phoenix", 33.446, -112.071, "America/Phoenix")],
        "POR": [(1970, 2100, "Portland", 45.532, -122.667, "America/Los_Angeles")],
        "SAC": [(1985, 2100, "Sacramento", 38.580, -121.500, "America/Los_Angeles")],
        "SAS": [(1976, 2100, "San Antonio", 29.427, -98.437, "America/Chicago")],
        "TOR": [(1995, 2100, "Toronto", 43.643, -79.379, "America/Toronto")],
        "UTA": [(1979, 2100, "Salt Lake City", 40.768, -111.901, "America/Denver")],
        "WAS": [(1973, 2100, "Washington", 38.898, -77.021, "America/New_York")],
    },
}
# Games played away from the home venue: (league, first date, last date, team or "*", city, lat, lon, tz, no_crowd, neutral)
OVERRIDES = [
    ("wnba", "2020-07-01", "2020-10-31", "*", "Bradenton (bubble)", 27.440, -82.570, "America/New_York", True, True),
    ("nba", "2020-07-30", "2020-10-12", "*", "Orlando (bubble)", 28.337, -81.554, "America/New_York", True, True),
    ("nba", "2020-12-01", "2021-06-30", "TOR", "Tampa", 27.943, -82.452, "America/New_York", False, False),
]
# Seasons with no or limited crowds outside the bubbles (attendance = 0 or missing is kept as reported)
LIMITED_CROWD = {"nba": [("2020-12-01", "2021-05-31")], "wnba": [("2021-05-01", "2021-09-30")]}


def _venue(league: str, team: str, date: pd.Timestamp):
    for lg, d0, d1, t, city, lat, lon, tz, no_crowd, neutral in OVERRIDES:
        if lg == league and (t == "*" or t == team) and pd.Timestamp(d0) <= date <= pd.Timestamp(d1):
            return city, lat, lon, tz, no_crowd, neutral
    year = date.year if league == "wnba" or date.month >= 8 else date.year - 1
    for first, last, city, lat, lon, tz in VENUES.get(league, {}).get(team, []):
        if first <= year <= last:
            return city, lat, lon, tz, False, False
    return None, np.nan, np.nan, None, False, False


def _km(lat1, lon1, lat2, lon2) -> float:
    if any(pd.isna(v) for v in (lat1, lon1, lat2, lon2)):
        return np.nan
    p1, p2, dl, dp = radians(lat1), radians(lat2), radians(lon2 - lon1), radians(lat2 - lat1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


TZ_NAMES = {"eastern": "America/New_York", "central": "America/Chicago", "mountain": "America/Denver",
            "pacific": "America/Los_Angeles", "arizona": "America/Phoenix"}


def _tz(name, fallback):
    """IANA time zone: the venue table's, unless the source gives a full IANA name."""
    if isinstance(name, str) and "/" in name:
        return name
    return fallback


def _offset(tz: str | None, date: pd.Timestamp) -> float:
    if not tz:
        return np.nan
    return ZoneInfo(tz).utcoffset(datetime(date.year, date.month, date.day, 19)).total_seconds() / 3600


def _local_hour(tip_et, tz, date, tip_utc=None) -> float:
    """Local tip-off hour at the venue: from the UTC time when known, else from '7:00 PM ET'."""
    if not tz:
        return np.nan
    if isinstance(tip_utc, str) and tip_utc:
        try:
            t = pd.Timestamp(tip_utc)
            t = t.tz_localize("UTC") if t.tzinfo is None else t
            loc = t.tz_convert(tz)
            return loc.hour + loc.minute / 60
        except Exception:  # noqa: BLE001
            pass
    if not isinstance(tip_et, str):
        return np.nan
    try:
        t = datetime.strptime(tip_et.split()[0] + " " + tip_et.split()[1], "%I:%M %p")
    except (ValueError, IndexError):
        return np.nan
    et = datetime(date.year, date.month, date.day, t.hour, t.minute, tzinfo=ZoneInfo("America/New_York"))
    loc = et.astimezone(ZoneInfo(tz))
    return loc.hour + loc.minute / 60


def build(tag: str, out: Path = OUT) -> pd.DataFrame:
    league = tag.rpartition("_")[0]
    g = pd.read_csv(out / f"{tag}_games.csv", dtype={"game_id": str})
    g["date"] = pd.to_datetime(g["date"])
    g = g.sort_values(["date", "game_id"])
    rows = []
    for r in g.itertuples():
        city, lat, lon, tz, no_crowd, neutral = _venue(league, r.home, r.date)
        tz = _tz(getattr(r, "arena_tz", None), tz)
        src_neutral = getattr(r, "neutral_site", None)
        if src_neutral is True or str(src_neutral).lower() == "true":
            neutral = True
        base = {"competition": league, "season": r.season, "game_id": r.game_id, "date": r.date,
                "season_type": getattr(r, "season_type", None), "venue_city": city, "lat": lat, "lon": lon, "tz": tz,
                "attendance": getattr(r, "attendance", np.nan), "national_tv": getattr(r, "national_tv", None),
                "tip_local_h": _local_hour(getattr(r, "tip_et", None), tz, r.date, getattr(r, "tip_utc", None)),
                "no_crowd": bool(no_crowd), "neutral": bool(neutral)}
        rows.append({**base, "team": r.home, "opponent": r.away, "is_home": not neutral})
        rows.append({**base, "team": r.away, "opponent": r.home, "is_home": False})
    c = pd.DataFrame(rows)
    unknown = sorted(set(c.loc[c["lat"].isna(), "team"].dropna()) | set(g.loc[~g["home"].isin(VENUES.get(league, {})), "home"]))
    if unknown:
        print(f"  ! {tag}: no venue for {unknown} (add them to VENUES in warehouse/context.py)")
    c["attendance"] = pd.to_numeric(c["attendance"], errors="coerce")
    for lim0, lim1 in LIMITED_CROWD.get(league, []):
        c.loc[c["date"].between(lim0, lim1), "limited_crowd"] = True
    c["limited_crowd"] = c.get("limited_crowd", pd.Series(False, index=c.index)).fillna(False).astype(bool)
    home_max = c[c["is_home"]].groupby("team")["attendance"].max()
    host = np.where(c["is_home"], c["team"], c["opponent"])
    c["attendance_rel"] = c["attendance"] / pd.Series(host, index=c.index).map(home_max)
    # sequences per team
    out_rows = []
    for team, d in c.sort_values(["date", "game_id"]).groupby("team"):
        d = d.copy()
        prev_date = d["date"].shift(1)
        d["rest_days"] = (d["date"] - prev_date).dt.days - 1
        d["back_to_back"] = d["rest_days"] == 0
        d["games_last_7d"] = [((d["date"] < x) & (d["date"] >= x - pd.Timedelta(days=7))).sum() for x in d["date"]]
        home_city = _venue(league, team, d["date"].iloc[0])
        plat, plon, ptz = d["lat"].shift(1), d["lon"].shift(1), d["tz"].shift(1)
        plat, plon = plat.fillna(home_city[1]), plon.fillna(home_city[2])
        ptz = ptz.fillna(home_city[3])
        d["travel_km"] = [_km(a, b, c_, e) for a, b, c_, e in zip(plat, plon, d["lat"], d["lon"])]
        d["tz_shift_h"] = [_offset(tz, x) - _offset(p, x) for tz, p, x in zip(d["tz"], ptz, d["date"])]
        d["direction"] = np.select([d["tz_shift_h"] > 0, d["tz_shift_h"] < 0], ["east", "west"], "none")
        trip, stand, tc, sc = [], [], 0, 0
        for h in d["is_home"]:
            tc, sc = (0, sc + 1) if h else (tc + 1, 0)
            trip.append(tc)
            stand.append(sc)
        d["trip_game"], d["stand_game"] = trip, stand
        out_rows.append(d)
    ctx = pd.concat(out_rows).sort_values(["date", "game_id", "is_home"], ascending=[True, True, False])
    ctx.to_csv(out / f"{tag}_context.csv", index=False)
    return ctx


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tags", nargs="*", help="e.g. wnba_2025 nba_2024")
    ap.add_argument("--all", help="every built season of this league, e.g. wnba")
    args = ap.parse_args()
    tags = list(args.tags)
    if args.all:
        tags += sorted(p.name[:-len("_games.csv")] for p in OUT.glob(f"{args.all}_*_games.csv"))
    for tag in tags:
        c = build(tag)
        print(f"{tag}: {len(c)} team-games · median travel {c['travel_km'].median():.0f} km · "
              f"back-to-backs {int(c['back_to_back'].sum())} · attendance known {c['attendance'].notna().mean():.0%}")


if __name__ == "__main__":
    main()
