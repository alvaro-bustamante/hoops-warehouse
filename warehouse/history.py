"""Build many seasons of the NBA or the WNBA in one go, with game context and player bios.

    python -m warehouse.history wnba --from 2015 --to 2025          # WNBA, ten-plus seasons
    python -m warehouse.history wnba --from 2025 --to 2025 --limit 5   # a 2-minute test first
    python -m warehouse.history nba --from 2016 --to 2025           # NBA (much bigger: run it over several nights)

It can be stopped at any time and run again: every download is cached in data/raw/<league>/ and
data/raw/bios/, so the next run only asks for what is missing. Progress and errors are written to
data/warehouse/<league>_history_log.txt.

Output per season (data/warehouse/): <league>_<year>_{events,stints,possessions,games,team_games,
player_games,context}.  Once for the league: <league>_players.csv (date of birth, height, position,
first season, draft, country).
"""
from __future__ import annotations

import argparse
import time
import traceback
from pathlib import Path

import pandas as pd

from . import context
from .build import OUT, run_nba
from .sources import nba


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("league", choices=["wnba", "nba"])
    ap.add_argument("--from", dest="first", type=int, required=True)
    ap.add_argument("--to", dest="last", type=int, required=True)
    ap.add_argument("--limit", type=int, help="Only the first N games of each season (a quick test).")
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between requests (be gentle with stats.nba.com).")
    ap.add_argument("--no-bios", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    log = OUT / f"{args.league}_history_log.txt"

    def note(msg: str) -> None:
        print(msg, flush=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M')}  {msg}\n")

    note(f"===== {args.league} {args.first}-{args.last}" + (f" (limit {args.limit})" if args.limit else ""))
    for year in range(args.last, args.first - 1, -1):                # newest first
        t0 = time.time()
        try:
            tag = run_nba(str(year), args.limit, args.league, playoffs=True, delay=args.delay)
            if not tag:
                note(f"{args.league} {year}: no games")
                continue
            g = pd.read_csv(OUT / f"{tag}_games.csv")
            ctx = context.build(tag)
            note(f"{tag}: {len(g)} games · exact box {int((g['box_mismatches'] == 0).sum())} · "
                 f"right score {int(g['score_ok'].sum())} · attendance known "
                 f"{ctx['attendance'].notna().mean():.0%} · {(time.time() - t0) / 60:.0f} min")
        except KeyboardInterrupt:
            note("stopped by the user: run the same command again to continue")
            raise
        except Exception as exc:  # noqa: BLE001  one season failing must not stop the others
            note(f"{args.league} {year}: ERROR {exc}\n{traceback.format_exc(limit=3)}")
    if not args.no_bios:
        ids = set()
        for f in OUT.glob(f"{args.league}_*_player_games.csv"):
            ids |= set(pd.read_csv(f, usecols=["player_id"], dtype={"player_id": str})["player_id"].dropna())
        ids = {i for i in ids if i.isdigit()}
        note(f"player bios: {len(ids)} players")
        bios = nba.player_bios(sorted(ids), args.league, delay=args.delay * 0.8)
        if len(bios):
            bios.to_csv(OUT / f"{args.league}_players.csv", index=False)
            note(f"{args.league}_players.csv: {len(bios)} players, birthdate known {bios['birthdate'].notna().mean():.0%}")
    note("done")


if __name__ == "__main__":
    main()
