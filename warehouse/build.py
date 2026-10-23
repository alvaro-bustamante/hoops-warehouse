"""Build the warehouse tables for a competition and season.

    python -m warehouse.build euroleague --season 2025
    python -m warehouse.build eurocup --season 2025 --games 1-40
    python -m warehouse.build nba --season 2025-26 --limit 100

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


def run_nba(season: str, limit: int | None) -> None:
    from .sources import nba
    games = nba.season_games(season)
    if limit:
        games = games.head(limit)
    results, info = [], []
    for row in games.itertuples():
        try:
            doc = nba.fetch_game(row.GAME_ID)
            _, players = nba.parse_box(doc)
            res = build_game(nba.parse_events(doc, season, row.GAME_ID), players)
        except Exception as exc:  # noqa: BLE001
            print(f"  {row.GAME_ID}: skipped ({exc})")
            continue
        results.append(res)
        info.append({"competition": "nba", "season": season, "game_id": row.GAME_ID, "date": row.GAME_DATE,
                     "matchup": row.MATCHUP, **res["report"]})
        print(f"  {row.GAME_ID} {row.MATCHUP} ok")
    save(f"nba_{season}", results, info)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("competition", choices=["euroleague", "eurocup", "nba"])
    ap.add_argument("--season", required=True, help="2025 for EuroLeague/EuroCup 2025-26; 2025-26 for the NBA.")
    ap.add_argument("--games", help="EuroLeague/EuroCup game codes, e.g. 1-40 (default: all played games).")
    ap.add_argument("--limit", type=int, help="NBA: only the first N games of the season.")
    args = ap.parse_args()
    if args.competition == "nba":
        run_nba(args.season, args.limit)
    else:
        run_euroleague(args.competition, int(args.season), _range(args.games))


if __name__ == "__main__":
    main()
