"""First look at a built season: checks, best lineups and assist pairs.

    python -m warehouse.explore euroleague 2025
    python -m warehouse.explore acb 2026 --min-poss 60
    python -m warehouse.explore euroleague 2025 --team MAD

Lineup net ratings are shown raw and shrunk towards zero with a simple
normal-normal model (sampling SD of a per-100 rating ≈ 125/√possessions):
small samples get pulled in, as in the 2D Explorer.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

WH = Path("data") / "warehouse"


def load(tag: str, name: str) -> pd.DataFrame:
    for ext in (".csv.gz", ".csv"):
        path = WH / f"{tag}_{name}{ext}"
        if path.exists():
            return pd.read_csv(path, low_memory=False,       # ids are text, never numbers (ACB ids are digits)
                               dtype={"player_id": str, "game_id": str, "passer": str, "scorer": str})
    raise SystemExit(f"Missing {WH / (tag + '_' + name)}: build the season first.")


def player_names(events: pd.DataFrame) -> dict:
    ev = events.dropna(subset=["player_id", "player_name"])
    return ev.groupby("player_id")["player_name"].agg(lambda s: s.mode().iat[0]).to_dict()


def lineup_table(stints: pd.DataFrame, team: str | None) -> pd.DataFrame:
    rows = []
    for side, other in (("home", "away"), ("away", "home")):
        s = stints.assign(team=stints[side], lineup=stints[f"{side}_on"], pf=stints[f"pts_{side}"],
                          pa=stints[f"pts_{other}"], poss_f=stints[f"poss_{side}"], poss_a=stints[f"poss_{other}"])
        rows.append(s[["team", "lineup", "pf", "pa", "poss_f", "poss_a", "seconds"]])
    L = pd.concat(rows).groupby(["team", "lineup"]).sum().reset_index()
    if team:
        L = L[L["team"] == team]
    L["poss"] = (L.poss_f + L.poss_a) / 2
    L = L[L["poss"] > 0]
    L["ortg"] = 100 * L.pf / L.poss_f.clip(lower=1)
    L["drtg"] = 100 * L.pa / L.poss_a.clip(lower=1)
    L["net"] = L.ortg - L.drtg
    # normal-normal shrinkage of the net rating towards 0 (the average lineup)
    se2 = (125 / np.sqrt(L["poss"])) ** 2
    tau2 = max(np.average(L["net"] ** 2, weights=L["poss"]) - np.average(se2, weights=L["poss"]), 4.0)
    L["net_bayes"] = L["net"] * tau2 / (tau2 + se2)
    L["net_sd"] = np.sqrt(tau2 * se2 / (tau2 + se2))
    L["minutes"] = L["seconds"] / 60
    return L


def assist_pairs(events: pd.DataFrame) -> pd.DataFrame:
    """Passer -> scorer for every assist: the made shot closest in time (same team), or the free
    throws it led to when there is no basket."""
    ev = events.reset_index(drop=True)
    made = ev[ev["action"].isin(["fg2_made", "fg3_made", "ft_made"])]   # EuroLeague also credits assists on fouls
    rows = []
    for i, a in ev[ev["action"] == "ast"].iterrows():
        cand = made[(made["game_id"] == a["game_id"]) & (made["team"] == a["team"]) &
                    ((made["elapsed_s"] - a["elapsed_s"]).abs() <= 3)]
        if len(cand):
            fg = cand[cand["action"] != "ft_made"]
            pool = fg if len(fg) else cand
            j = (pool.index.to_series() - i).abs().idxmin()
            rows.append({"team": a["team"], "passer": a["player_id"], "scorer": cand.at[j, "player_id"],
                         "points": int(cand.at[j, "points"])})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("competition")
    ap.add_argument("season")
    ap.add_argument("--team", help="Only this team code.")
    ap.add_argument("--min-poss", type=float, default=100, help="Minimum possessions for the lineup table.")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    tag = f"{args.competition}_{args.season}"
    games, events, stints = load(tag, "games"), load(tag, "events"), load(tag, "stints")
    names = player_names(events)
    short = lambda ids: " · ".join(str(names.get(p, p)).split(",")[0].title() for p in str(ids).split("|"))  # noqa

    print(f"\n== {tag}: {len(games)} games")
    if "box_mismatches" in games:
        print(f"   exact box score: {(games['box_mismatches'] == 0).sum()}/{len(games)}"
              f"   ·   right final score: {games['score_ok'].sum()}/{len(games)}")

    L = lineup_table(stints, args.team)
    big = L[L["poss"] >= args.min_poss].sort_values("net_bayes", ascending=False)
    print(f"\n== Best lineups (≥ {args.min_poss:.0f} possessions), net rating per 100 possessions")
    print(f"   {'team':5} {'poss':>5} {'min':>5} {'net':>7} {'Bayes':>7} {'± sd':>6}  lineup")
    for r in big.head(args.top).itertuples():
        print(f"   {r.team:5} {r.poss:5.0f} {r.minutes:5.0f} {r.net:+7.1f} {r.net_bayes:+7.1f} {r.net_sd:6.1f}  {short(r.lineup)}")
    print(f"\n   {len(big)} lineups reach the threshold; {len(L)} lineups in total.")

    pairs = assist_pairs(events if not args.team else events[events["team"] == args.team])
    if len(pairs):
        top = pairs.groupby(["team", "passer", "scorer"]).agg(assists=("points", "size"), points=("points", "sum"))
        top = top.sort_values("assists", ascending=False).head(args.top).reset_index()
        print("\n== Most frequent assist pairs (passer -> scorer)")
        for r in top.itertuples():
            print(f"   {r.team:5} {short(r.passer):>22} -> {short(r.scorer):<22} {r.assists:3d} assists, {r.points} pts")
        scope = events if not args.team else events[events["team"] == args.team]
        print(f"\n   {len(pairs)} assisted baskets linked to their scorer "
              f"({len(pairs) / max((scope['action'] == 'ast').sum(), 1):.0%} of all assists).")


if __name__ == "__main__":
    main()
