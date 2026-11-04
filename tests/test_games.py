"""Data-quality audit on the EuroLeague / EuroCup games already downloaded to data/raw/.

It rebuilds every game and checks the rates over all of them, so it is slow (about half an hour
for ten seasons) and only runs when asked:

    HW_DATA_TESTS=1 python -m pytest tests/test_games.py -q          # macOS / Linux
    $env:HW_DATA_TESTS=1; python -m pytest tests/test_games.py -q    # Windows PowerShell

Add HW_DATA_SAMPLE=300 to check a random sample of games instead of all of them.
"""
import json
import os
import random
from pathlib import Path

import pytest

from warehouse.pipeline import build_game
from warehouse.sources import euroleague as el

RAW = Path("data/raw/euroleague")
pytestmark = pytest.mark.skipif(os.environ.get("HW_DATA_TESTS") != "1" or not RAW.exists(),
                                reason="data audit: set HW_DATA_TESTS=1 (needs data/raw/euroleague)")


def _games() -> list[str]:
    tags = sorted({p.name.rsplit("_", 1)[0] for p in RAW.glob("*_PlayByPlay.json")})
    n = int(os.environ.get("HW_DATA_SAMPLE", 0))
    return random.Random(0).sample(tags, min(n, len(tags))) if n else tags


@pytest.fixture(scope="module")
def reports():
    out = []
    for tag in _games():
        code, game = tag.split("_")
        doc = {n: json.loads((RAW / f"{tag}_{n}.json").read_text(encoding="utf-8"))
               for n in ("Header", "PlayByPlay", "Points", "Boxscore")}
        _, players = el.parse_box(doc)
        comp = "euroleague" if code.startswith("E") else "eurocup"
        res = build_game(el.parse_events(doc, comp, int(code[1:]), int(game)), players)
        rep = res["report"]
        rep["stint_gap_s"] = abs(res["stints"]["seconds"].sum() - res["events"]["elapsed_s"].max())
        a, b = rep["possessions"].values()
        rep["possession_gap"] = abs(a - b)
        out.append(rep)
    assert out, "no games in data/raw/euroleague"
    return out


def _share(reports, ok) -> float:
    return sum(bool(ok(r)) for r in reports) / len(reports)


def test_final_score(reports):
    assert _share(reports, lambda r: r["score_ok"]) >= 0.995


def test_player_box_scores(reports):
    assert _share(reports, lambda r: r.get("box_mismatches") == 0) >= 0.99


def test_minutes_within_a_minute(reports):
    # lineups are inferred: a missing substitution in the feed shifts some seconds between players
    assert _share(reports, lambda r: r.get("minutes_max_gap_s", 0) <= 60) >= 0.97


def test_stints_cover_the_game(reports):
    assert _share(reports, lambda r: r["stint_gap_s"] < 1) >= 0.995


def test_possessions_alternate(reports):
    assert _share(reports, lambda r: r["possession_gap"] <= 3) >= 0.98
