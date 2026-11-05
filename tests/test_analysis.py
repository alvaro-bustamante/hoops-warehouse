"""The Gibbs sampler of the schedule model recovers known effects from simulated games."""
import numpy as np
import pandas as pd

from analysis.schedule_effects import design, gibbs


def simulate(n_games=4000, seed=3):
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(20)]
    strength = dict(zip(teams, rng.normal(0, 4, len(teams))))
    home = rng.choice(teams, n_games)
    away = np.array([rng.choice([t for t in teams if t != h]) for h in home])
    df = pd.DataFrame({"season": 2024, "home": home, "away": away, "neutral": False, "no_crowd": False,
                       "limited_crowd": False, "rest_home": rng.choice([0, 1, 2, 3], n_games, p=[.2, .6, .1, .1]),
                       "rest_away": rng.choice([0, 1, 2, 3], n_games, p=[.2, .6, .1, .1]),
                       "g7_home": rng.integers(2, 5, n_games), "g7_away": rng.integers(2, 5, n_games),
                       "km_home": rng.uniform(0, 3000, n_games), "km_away": rng.uniform(0, 3000, n_games),
                       "tz_away": rng.choice([-2, -1, 0, 1, 2], n_games)})
    mu = (2.5 - 2.0 * (df["rest_home"] == 0) + 2.0 * (df["rest_away"] == 0)
          + df["home"].map(strength) - df["away"].map(strength))
    df["margin"] = mu + rng.normal(0, 12, n_games)
    return df


def test_recovers_home_court_and_back_to_backs():
    df = simulate()
    X, blocks = design(df)
    fit = gibbs(X, blocks, df["margin"].to_numpy(float), draws=600, burn=200)
    S = fit.summary()
    assert abs(S.loc["hca_2024", "mean"] - 2.5) < 1.0
    assert abs(S.loc["b2b_home", "mean"] + 2.0) < 1.0
    assert abs(S.loc["b2b_away", "mean"] - 2.0) < 1.0
    assert abs(fit.sigma.mean() - 12) < 0.6
    # no effect was simulated for travel: its interval should include zero
    assert S.loc["travel_away", "q0.05"] < 0 < S.loc["travel_away", "q0.95"]
