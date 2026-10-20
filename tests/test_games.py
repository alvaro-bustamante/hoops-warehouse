"""Checks on games already downloaded to data/raw/ (skipped if there are none).

    python -m pytest tests
"""
import json
from pathlib import Path

import pytest

from warehouse.pipeline import build_game
from warehouse.sources import euroleague as el

RAW = Path("data/raw/euroleague")
GAMES = sorted({p.name.rsplit("_", 1)[0] for p in RAW.glob("*_PlayByPlay.json")}) if RAW.exists() else []


@pytest.mark.parametrize("tag", GAMES or [pytest.param(None, marks=pytest.mark.skip("no games downloaded"))])
def test_euroleague_game_matches_official_box(tag):
    code, game = tag.split("_")
    doc = {n: json.loads((RAW / f"{tag}_{n}.json").read_text(encoding="utf-8"))
           for n in ("Header", "PlayByPlay", "Points", "Boxscore")}
    _, players = el.parse_box(doc)
    comp = "euroleague" if code.startswith("E") else "eurocup"
    res = build_game(el.parse_events(doc, comp, int(code[1:]), int(game)), players)
    rep = res["report"]
    assert rep["score_ok"]
    assert rep["box_mismatches"] == 0
    assert rep["minutes_max_gap_s"] <= 5
    assert abs(res["stints"]["seconds"].sum() - res["events"]["elapsed_s"].max()) < 1
    a, b = rep["possessions"].values()
    assert abs(a - b) <= 3
