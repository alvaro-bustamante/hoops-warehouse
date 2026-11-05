"""What the schedule costs in the NBA: rest, back-to-backs, travel, time zones and home court.

    python -m analysis.schedule_effects                         # NBA 2015-16 → 2025-26, writes analysis/out/
    python -m analysis.schedule_effects --league wnba --from 2015 --to 2025
    python -m analysis.schedule_effects --draws 4000 --holdout 0.25

Model (one row per game, from the home team's side):

    margin = HCA[season] · home_court  +  η[franchise] · home_court        (home court, per season and arena)
           + bubble · neutral_site + γ_empty · empty_arena + γ_limited · limited_crowd
           + β · schedule (back-to-backs, extra rest, travel, time-zone shifts, games in the last 7 days)
           + θ[home team, season] − θ[away team, season]                  (team strength)
           + ε,     ε ~ Normal(0, σ²)

θ ~ Normal(0, σθ²) and η ~ Normal(0, ση²) are random effects (partial pooling: a team with few games,
or an arena with an unusual record, is pulled towards the league); the fixed effects have
Normal(0, 10²) priors and the variances inverse-gamma priors. Everything is conjugate, so the
posterior is sampled exactly with a blocked Gibbs sampler (numpy only, no PyMC/Stan needed):
all coefficients jointly from their multivariate normal, then each variance.

Validation: the last `--holdout` share of each regular season (by date) is held out; the model is fit
on the rest and compared with a strength-and-home-court-only model on margin error, win
probability Brier score and log loss.

Data: NBA Stats (nba_api) through hoops-warehouse. Games whose rebuilt score does not match the
official one are left out.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

WAREHOUSE = Path("data/warehouse")
OUT = Path("analysis/out")

SCHEDULE = {   # column -> label (effects are in points of home margin)
    "b2b_home": "Home team on a back-to-back",
    "b2b_away": "Away team on a back-to-back",
    "rest2_home": "Home team with 2+ days of rest",
    "rest2_away": "Away team with 2+ days of rest",
    "dense_home": "Home team: games in the last 7 days (each)",
    "dense_away": "Away team: games in the last 7 days (each)",
    "travel_away": "Away team travel to the game (per 1,000 km)",
    "travel_home": "Home team travel back home (per 1,000 km)",
    "east_away": "Away team moved east (per hour of time zone)",
    "west_away": "Away team moved west (per hour of time zone)",
}
CROWD = {"empty_arena": "Home court with no fans (2020-21)", "limited_crowd": "Home court in 2020-21 (arenas empty or with limited fans)",
         "bubble": "Designated home team in the bubble (neutral)"}


# ------------------------------------------------------------------------------------------------ data
def load_games(league: str = "nba", first: int = 2015, last: int = 2025, base: Path = WAREHOUSE,
               playoffs: bool = False) -> pd.DataFrame:
    """One row per game with the margin and the schedule of both teams (from the context tables)."""
    rows = []
    for season in range(first, last + 1):
        g, tg, c = (base / f"{league}_{season}_{t}.csv" for t in ("games", "team_games", "context"))
        if not (g.exists() and tg.exists() and c.exists()):
            continue
        games = pd.read_csv(g, dtype={"game_id": str})
        team = pd.read_csv(tg, dtype={"game_id": str})
        ctx = pd.read_csv(c, dtype={"game_id": str})
        if not playoffs:
            games = games[games["season_type"].eq("Regular Season")]
        games = games[games["score_ok"].astype(bool)]                     # rebuilt score = official score
        pts = team.set_index(["game_id", "team"])["pts"]
        c = ctx.set_index(["game_id", "team"])
        for r in games.itertuples():
            h, a = r.home, r.away
            if (r.game_id, h) not in c.index or (r.game_id, a) not in c.index:
                continue
            ch, ca = c.loc[(r.game_id, h)], c.loc[(r.game_id, a)]
            rows.append({"season": season, "game_id": r.game_id, "date": r.date, "home": h, "away": a,
                         "margin": float(pts.get((r.game_id, h), np.nan) - pts.get((r.game_id, a), np.nan)),
                         "neutral": bool(ch["neutral"]), "no_crowd": bool(ch["no_crowd"]),
                         "limited_crowd": bool(ch["limited_crowd"]),
                         "rest_home": ch["rest_days"], "rest_away": ca["rest_days"],
                         "g7_home": ch["games_last_7d"], "g7_away": ca["games_last_7d"],
                         "km_home": ch["travel_km"], "km_away": ca["travel_km"],
                         "tz_away": ca["tz_shift_h"]})
    df = pd.DataFrame(rows).dropna(subset=["margin"])
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["date", "game_id"]).reset_index(drop=True)


def design(df: pd.DataFrame, schedule: bool = True):
    """Fixed-effect matrix X, random-effect blocks Z (team-season strength, franchise home court)."""
    rest_h = df["rest_home"].fillna(3).clip(upper=3)          # first game of the season: rested
    rest_a = df["rest_away"].fillna(3).clip(upper=3)
    home_court = (~df["neutral"]).astype(float)
    fixed = {}
    seasons = sorted(df["season"].unique())
    full = ~df["neutral"] & ~df["no_crowd"] & ~df["limited_crowd"]
    for s in seasons:                                           # home advantage with fans, season by season
        col = ((df["season"] == s) & full).astype(float)
        if col.sum() >= 50:
            fixed[f"hca_{s}"] = col
    # home advantage without fans / with few fans / in the bubble: absolute, not added to a season's
    special = {"bubble": df["neutral"],
               "empty_arena": df["no_crowd"] & ~df["neutral"],
               "limited_crowd": df["limited_crowd"] & ~df["no_crowd"] & ~df["neutral"]}
    for key, col in special.items():
        if col.sum() >= 30:                                     # only situations the league actually had
            fixed[key] = col.astype(float)
    if schedule:
        fixed.update({
            "b2b_home": (rest_h == 0).astype(float), "b2b_away": (rest_a == 0).astype(float),
            "rest2_home": (rest_h >= 2).astype(float), "rest2_away": (rest_a >= 2).astype(float),
            "dense_home": df["g7_home"].astype(float) - 3, "dense_away": df["g7_away"].astype(float) - 3,
            "travel_away": df["km_away"].fillna(0) / 1000, "travel_home": df["km_home"].fillna(0) / 1000,
            "east_away": df["tz_away"].clip(lower=0).fillna(0), "west_away": (-df["tz_away"]).clip(lower=0).fillna(0),
        })
    X = pd.DataFrame(fixed)
    # team strength: +1 for the home team-season, −1 for the away team-season
    ts = sorted(set(df["home"] + "_" + df["season"].astype(str)) | set(df["away"] + "_" + df["season"].astype(str)))
    idx = {k: i for i, k in enumerate(ts)}
    Zt = np.zeros((len(df), len(ts)))
    hi = (df["home"] + "_" + df["season"].astype(str)).map(idx).to_numpy()
    ai = (df["away"] + "_" + df["season"].astype(str)).map(idx).to_numpy()
    Zt[np.arange(len(df)), hi] = 1
    Zt[np.arange(len(df)), ai] = -1
    # franchise home court: deviation of each arena from the league's home advantage
    fr = sorted(df["home"].unique())
    fidx = {k: i for i, k in enumerate(fr)}
    Zh = np.zeros((len(df), len(fr)))
    Zh[np.arange(len(df)), df["home"].map(fidx).to_numpy()] = home_court.to_numpy()
    return X, [("team_strength", ts, Zt), ("franchise_hca", fr, Zh)]


# ------------------------------------------------------------------------------------------------ model
@dataclass
class Fit:
    names: list[str]
    blocks: list[tuple[str, list[str], slice]]
    draws: np.ndarray              # (draws, coefficients)
    sigma: np.ndarray              # residual SD per draw
    tau: dict                      # block name -> SD per draw

    def summary(self, prob: float = 0.9) -> pd.DataFrame:
        lo, hi = (1 - prob) / 2, 1 - (1 - prob) / 2
        d = self.draws
        return pd.DataFrame({"mean": d.mean(0), "sd": d.std(0), f"q{lo:.2f}": np.quantile(d, lo, 0),
                             f"q{hi:.2f}": np.quantile(d, hi, 0), "p_positive": (d > 0).mean(0)},
                            index=self.names)

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Posterior mean margin and predictive SD (parameter uncertainty + game noise)."""
        mu = X @ self.draws.T
        return mu.mean(1), np.sqrt(mu.var(1) + (self.sigma ** 2).mean())


def gibbs(X: pd.DataFrame, blocks, y: np.ndarray, draws: int = 3000, burn: int = 500, seed: int = 1,
          prior_sd: float = 10.0, a0: float = 2.0, b0: float = 2.0) -> Fit:
    """Blocked Gibbs sampler for y = Xβ + Σ Z_k u_k + ε with u_k ~ N(0, τ_k² I)."""
    rng = np.random.default_rng(seed)
    M = np.hstack([X.to_numpy(float)] + [Z for _, _, Z in blocks])
    names = list(X.columns) + [f"{b}:{n}" for b, labels, _ in blocks for n in labels]
    p0 = X.shape[1]
    spans, start = [], p0
    for b, labels, Z in blocks:
        spans.append((b, labels, slice(start, start + Z.shape[1])))
        start += Z.shape[1]
    MtM, Mty = M.T @ M, M.T @ y
    n, p = M.shape
    coef = np.zeros(p)
    sigma2 = np.var(y)
    tau2 = {b: 4.0 for b, _, _ in spans}
    keep, keep_s, keep_t = [], [], {b: [] for b, _, _ in spans}
    for it in range(draws + burn):
        prec = np.full(p, 1 / prior_sd ** 2)
        for b, _, sl in spans:
            prec[sl] = 1 / tau2[b]
        Q = MtM / sigma2 + np.diag(prec)
        L = np.linalg.cholesky(Q)
        mean = np.linalg.solve(L.T, np.linalg.solve(L, Mty / sigma2))
        coef = mean + np.linalg.solve(L.T, rng.standard_normal(p))
        resid = y - M @ coef
        sigma2 = 1 / rng.gamma(a0 + n / 2, 1 / (b0 + resid @ resid / 2))
        for b, _, sl in spans:
            u = coef[sl]
            tau2[b] = 1 / rng.gamma(a0 + len(u) / 2, 1 / (b0 + u @ u / 2))
        if it >= burn:
            keep.append(coef.copy())
            keep_s.append(np.sqrt(sigma2))
            for b in tau2:
                keep_t[b].append(np.sqrt(tau2[b]))
    return Fit(names, spans, np.array(keep), np.array(keep_s), {b: np.array(v) for b, v in keep_t.items()})


def _matrix(df, schedule, fit_names):
    X, blocks = design(df.reset_index(drop=True), schedule)
    M = pd.concat([X] + [pd.DataFrame(Z, columns=[f"{b}:{n}" for n in labels]) for b, labels, Z in blocks], axis=1)
    return M.reindex(columns=fit_names, fill_value=0.0).to_numpy(float)


def validate(df: pd.DataFrame, holdout: float = 0.25, draws: int = 1500) -> pd.DataFrame:
    """Fit on the first part of each season, predict the last `holdout` share (by date)."""
    rank = df.groupby("season")["date"].rank(pct=True, method="first")
    train = df[rank <= 1 - holdout].reset_index(drop=True)
    test = df[rank > 1 - holdout].reset_index(drop=True)
    rows = []
    for label, schedule in (("strength + home court", False), ("+ schedule and crowd", True)):
        X, blocks = design(train, schedule)
        fit = gibbs(X, blocks, train["margin"].to_numpy(float), draws=draws, burn=300)
        mu, sd = fit.predict(_matrix(test, schedule, fit.names))
        y = test["margin"].to_numpy(float)
        from scipy.stats import norm
        p = np.clip(norm.cdf(mu / sd), 1e-6, 1 - 1e-6)
        win = (y > 0).astype(float)
        rows.append({"model": label, "games": len(test), "MAE": np.abs(y - mu).mean(),
                     "RMSE": np.sqrt(((y - mu) ** 2).mean()), "Brier": ((p - win) ** 2).mean(),
                     "log loss": -(win * np.log(p) + (1 - win) * np.log(1 - p)).mean(),
                     "accuracy": ((p > 0.5) == (win == 1)).mean()})
    return pd.DataFrame(rows).set_index("model")


# ------------------------------------------------------------------------------------------------ report
def figures(fit: Fit, df: pd.DataFrame, out: Path, league: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    S = fit.summary()
    lo, hi = S.columns[2], S.columns[3]
    ink, accent, grey = "#111827", "#2563EB", "#9CA3AF"

    # 1 · home advantage by season, with the bubble and the empty arenas
    hca = S[S.index.str.startswith("hca_")]
    seasons = [int(i.split("_")[1]) for i in hca.index]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=160)
    ax.errorbar(seasons, hca["mean"], yerr=[hca["mean"] - hca[lo], hca[hi] - hca["mean"]], fmt="o-", color=accent,
                lw=1.8, capsize=3, label="Home court (fans present)")
    raw = df[~df["neutral"]].groupby("season")["margin"].mean()
    ax.plot(raw.index, raw.values, "o", color=grey, ms=4, label="Raw home margin")
    special = (("bubble", 2019.15, "D", "#7C3AED", "Bubble 2019-20 (neutral court)"),
               ("empty_arena", 2019.85, "s", "#DC2626", "No fans 2020-21"),
               ("limited_crowd", 2020.0, "^", "#F59E0B", "2020-21: empty or limited fans"))
    for key, xpos, mark, col, lab in special:
        if key in S.index and (fit.draws[:, fit.names.index(key)] != 0).any():
            m = S.loc[key, "mean"]
            ax.errorbar([xpos], [m], yerr=[[m - S.loc[key, lo]], [S.loc[key, hi] - m]], fmt=mark, color=col,
                        capsize=3, label=lab)
    ax.axhline(0, color=grey, lw=0.8, ls=":")
    ticks = sorted(set(seasons) | {2019, 2020})
    ax.set_xticks(ticks, [f"{s}-{str(s + 1)[-2:]}" for s in ticks], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Points of home margin")
    ax.set_title(f"{league.upper()} home advantage by season (posterior mean, 90% interval)", loc="left",
                 fontsize=10, fontweight="bold", color=ink)
    ax.legend(fontsize=7, frameon=False, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "home_advantage_by_season.png")
    plt.close(fig)

    # 2 · schedule effects
    keys = [k for k in SCHEDULE if k in S.index]
    T = S.loc[keys].iloc[::-1]
    labels = [{**SCHEDULE, **CROWD}[k] for k in T.index]
    fig, ax = plt.subplots(figsize=(8, 0.42 * len(T) + 1.2), dpi=160)
    y = np.arange(len(T))
    col = [accent if (v > 0) else "#DC2626" for v in T["mean"]]
    ax.errorbar(T["mean"], y, xerr=[T["mean"] - T[lo], T[hi] - T["mean"]], fmt="none", ecolor=grey, capsize=2)
    ax.scatter(T["mean"], y, color=col, zorder=3, s=28)
    ax.axvline(0, color=grey, lw=0.8, ls=":")
    ax.set_yticks(y, labels, fontsize=8)
    ax.set_xlabel("Effect on the home team's margin (points)")
    ax.set_title("What the schedule costs (posterior mean, 90% interval)", loc="left", fontsize=10,
                 fontweight="bold", color=ink)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "schedule_effects.png")
    plt.close(fig)

    # 3 · home court by arena
    F = S[S.index.str.startswith("franchise_hca:")].copy()
    F.index = F.index.str.split(":").str[1]
    F = F.sort_values("mean")
    fig, ax = plt.subplots(figsize=(8, 6.5), dpi=160)
    y = np.arange(len(F))
    ax.errorbar(F["mean"], y, xerr=[F["mean"] - F[lo], F[hi] - F["mean"]], fmt="o", color=accent, ecolor=grey,
                ms=4, capsize=2)
    ax.axvline(0, color=grey, lw=0.8, ls=":")
    ax.set_yticks(y, F.index, fontsize=7)
    ax.set_xlabel("Home advantage above / below the league (points)")
    ax.set_title("Home court by franchise, partially pooled", loc="left", fontsize=10, fontweight="bold", color=ink)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "home_court_by_franchise.png")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league", default="nba")
    ap.add_argument("--from", dest="first", type=int, default=2015)
    ap.add_argument("--to", dest="last", type=int, default=2025)
    ap.add_argument("--draws", type=int, default=3000)
    ap.add_argument("--holdout", type=float, default=0.25)
    ap.add_argument("--warehouse", default=str(WAREHOUSE))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    out = Path(args.out) / args.league
    out.mkdir(parents=True, exist_ok=True)
    df = load_games(args.league, args.first, args.last, Path(args.warehouse))
    print(f"{len(df):,} games · {df['season'].nunique()} seasons")
    X, blocks = design(df)
    fit = gibbs(X, blocks, df["margin"].to_numpy(float), draws=args.draws)
    S = fit.summary()
    S.to_csv(out / "posterior_summary.csv")
    figures(fit, df, out, args.league)
    val = validate(df, args.holdout, draws=max(1000, args.draws // 2))
    val.to_csv(out / "validation.csv")
    meta = {"games": len(df), "seasons": [int(s) for s in sorted(df["season"].unique())],
            "residual_sd": float(fit.sigma.mean()), "team_strength_sd": float(fit.tau["team_strength"].mean()),
            "franchise_hca_sd": float(fit.tau["franchise_hca"].mean()), "draws": args.draws}
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    show = S[~S.index.str.contains(":")].round(2)
    with pd.option_context("display.width", 160):
        print(show.to_string())
        print("\nValidation on the last part of each season:\n" + val.round(3).to_string())
    print(f"\nSaved to {out.resolve()}")


if __name__ == "__main__":
    main()
