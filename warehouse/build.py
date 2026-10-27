"""Build the warehouse tables for a competition and season.

    python -m warehouse.build euroleague --season 2025
    python -m warehouse.build eurocup --season 2025 --games 1-40
    python -m warehouse.build nba --season 2025 --limit 100     # NBA 2025-26
    python -m warehouse.build wnba --season 2025                # WNBA 2025, regular season + playoffs

Raw downloads are cached in data/raw/ (a second run only reads the disk).
Output in data/warehouse/:
    <comp>_<season>_events.csv.gz       one row per event, with lineups and possession ids
    <comp>_<season>_stints.csv.gz       lineup stints (5 vs 5) with points and possessions
    <comp>_<season>_possessions.csv.gz  one row per possession
    <comp>_<season>_games.csv           game list and the checks against the official box score
    <comp>_<season>_team_games.csv      box score of every team in every game (see gametables.py)
    <comp>_<season>_player_games.csv    box score of every player in every game, with minutes and +/-
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .pipeline import build_game

OUT = Path("data") / "warehouse"


def save(tag: str, results: list[dict], games: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if not results:
        print("No games found.")
        return
    for name in ("events", "stints", "possessions"):
        pd.concat([r[name] for r in results], ignore_index=True).to_csv(OUT / f"{tag}_{name}.csv.gz", index=False)
    pd.DataFrame(games).to_csv(OUT / f"{tag}_games.csv", index=False)
    rep = pd.DataFrame(games)
    print(f"\nSaved {len(results)} games to {OUT}/{tag}_*")
    if "box_mismatches" in rep:
        print(f"  games matching the official box score exactly: {(rep['box_mismatches'] == 0).sum()}/{len(rep)}")
    print(f"  games with the right final score: {rep['score_ok'].sum()}/{len(rep)}")
    if "minutes_max_gap_s" in rep:
        ok = rep["official_minutes_ok"].fillna(True).astype(bool) if "official_minutes_ok" in rep else True
        good = rep[ok]
        print(f"  minutes within 5 s of the official sheet: {(good['minutes_max_gap_s'] <= 5).sum()}/{len(good)} games"
              f" (largest gap {good['minutes_max_gap_s'].max():.0f} s)")
        if len(rep) - len(good):
            print(f"  official sheets with inconsistent minutes (source error, not checked): {len(rep) - len(good)}")
    if "lineups_ok" in rep:
        print(f"  games with reliable lineups (for lineup / on-off analyses): {rep['lineups_ok'].sum()}/{len(rep)}")
    noted = rep[rep["notes"].astype(str) != "[]"]
    if len(noted):
        print(f"  games with lineup notes: {len(noted)} (see the 'notes' column)")
    from . import gametables
    n_t, n_p = gametables.build(tag, OUT)
    print(f"  game tables: {n_t} team rows, {n_p} player rows ({tag}_team_games.csv, {tag}_player_games.csv)")


def _range(text: str | None) -> list[int] | None:
    if not text:
        return None
    a, _, b = text.partition("-")
    return list(range(int(a), int(b or a) + 1))


def run_euroleague(competition: str, season: int, games: list[int] | None, max_missing: int = 5) -> None:
    from .sources import euroleague as el
    results, info, missing, g = [], [], 0, 0
    todo = iter(games) if games else None
    while True:
        g = next(todo, None) if todo else g + 1
        if g is None or (todo is None and missing >= max_missing):
            break
        doc = el.fetch_game(competition, season, g)
        played = doc is not None and any(doc["PlayByPlay"].get(k) for k in el.PERIOD_KEYS)
        if not played:
            missing += 1
            continue
        missing = 0
        try:
            _, players = el.parse_box(doc)
            res = build_game(el.parse_events(doc, competition, season, g), players)
        except Exception as exc:  # noqa: BLE001
            print(f"  game {g}: skipped ({exc})")
            continue
        results.append(res)
        info.append({**el.game_info(doc, competition, season, g), **res["report"]})
        print(f"  game {g}: {info[-1]['home']}-{info[-1]['away']} ok")
    save(f"{competition}_{season}", results, info)


def run_nba(season: str, limit: int | None, league: str = "nba", playoffs: bool = True, delay: float = 1.0) -> str:
    """NBA (season "2025-26" or 2025) or WNBA (season 2025). Regular season and playoffs.
    Saved as <league>_<starting year>_*; downloads are cached in data/raw/<league>/, so an
    interrupted run continues where it stopped."""
    from .sources import nba
    year = int(str(season)[:4])
    api_season = (f"{year}-{str(year + 1)[-2:]}" if league == "nba" else str(year))
    frames = []
    for kind in ["Regular Season"] + (["Playoffs"] if playoffs else []):
        g = nba.season_games(api_season, kind, league)
        if len(g):
            frames.append(g.assign(season_type=kind))
    if not frames:
        print(f"No {league.upper()} games found for {api_season}.")
        return ""
    games = pd.concat(frames).drop_duplicates("GAME_ID").sort_values(["GAME_DATE", "GAME_ID"])
    if limit:
        games = games.head(limit)
    print(f"{league.upper()} {api_season}: {len(games)} games", flush=True)
    results, info = [], []
    for i, row in enumerate(games.itertuples(), 1):
        try:
            doc = nba.fetch_game(row.GAME_ID, Path("data/raw") / league, delay=delay)
            _, players = nba.parse_box(doc)
            ev = nba.parse_events(doc, year, row.GAME_ID, league)
            res = build_game(ev, players)
            ctx = nba.game_context(doc)
        except Exception as exc:  # noqa: BLE001
            print(f"  {row.GAME_ID}: skipped ({exc})", flush=True)
            continue
        home = ev.loc[ev["is_home"] == True, "team"].dropna()  # noqa: E712
        away = ev.loc[ev["is_home"] == False, "team"].dropna()  # noqa: E712
        results.append(res)
        info.append({"competition": league, "season": year, "game_id": row.GAME_ID, "date": row.GAME_DATE,
                     "season_type": row.season_type, "matchup": row.MATCHUP,
                     "home": home.iloc[0] if len(home) else None, "away": away.iloc[0] if len(away) else None,
                     **ctx, **res["report"]})
        print(f"  [{i}/{len(games)}] {row.GAME_ID} {row.MATCHUP} ok", flush=True)
    tag = f"{league}_{year}"
    save(tag, results, info)
    return tag


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("competition", choices=["euroleague", "eurocup", "nba", "wnba"])
    ap.add_argument("--season", required=True,
                    help="Starting year: 2025 = EuroLeague/EuroCup/NBA 2025-26; WNBA 2025.")
    ap.add_argument("--games", help="EuroLeague/EuroCup game codes, e.g. 1-40 (default: all played games).")
    ap.add_argument("--limit", type=int, help="NBA/WNBA: only the first N games of the season (a quick test).")
    ap.add_argument("--no-playoffs", action="store_true", help="NBA/WNBA: regular season only.")
    args = ap.parse_args()
    if args.competition in ("nba", "wnba"):
        run_nba(args.season, args.limit, args.competition, playoffs=not args.no_playoffs)
    else:
        run_euroleague(args.competition, int(args.season), _range(args.games))


if __name__ == "__main__":
    main()
