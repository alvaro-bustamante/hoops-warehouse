"""EuroLeague and EuroCup official live API (public, no key needed).

    game data:  https://live.euroleague.net/api/{Header|PlayByPlay|Points|Boxscore}
                ?gamecode=<n>&seasoncode=<E|U><year>

Season code: E2025 = EuroLeague 2025-26, U2025 = EuroCup 2025-26.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

from ..schema import elapsed, finish

BASE = "https://live.euroleague.net/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; hoops-warehouse/0.1; research)"}
COMPETITIONS = {"euroleague": "E", "eurocup": "U"}
PERIOD_KEYS = ["FirstQuarter", "SecondQuarter", "ThirdQuarter", "ForthQuarter", "ExtraTime"]

ACTION_MAP = {
    "2FGM": "fg2_made", "2FGA": "fg2_missed", "3FGM": "fg3_made", "3FGA": "fg3_missed",
    "FTM": "ft_made", "FTA": "ft_missed",
    "O": "orb", "D": "drb", "AS": "ast", "TO": "tov", "ST": "stl", "FV": "blk", "AG": "blk_against",
    "CM": "foul", "OF": "foul_offensive", "CMU": "foul_unsportsmanlike", "CMT": "foul_technical",
    "CMTI": "foul_technical", "CCH": "foul_technical", "CMD": "foul_unsportsmanlike", "B": "foul_technical",   # B = bench foul
    "RV": "foul_drawn",
    "IN": "sub_in", "OUT": "sub_out", "JB": "jump_ball", "TOUT": "timeout", "TOUT_TV": "timeout",
    "BP": "period_start", "EP": "period_end", "EG": "game_end",
}


# ─────────────────────────────────────────────────────────────────── download
def fetch_game(competition: str, season: int, game: int, cache_dir: Path | None = Path("data/raw/euroleague"),
               delay: float = 1.0) -> dict | None:
    """The four JSON documents of one game (cached on disk). None if the game does not exist yet."""
    code = f"{COMPETITIONS[competition]}{season}"
    out = {}
    for name in ("Header", "PlayByPlay", "Points", "Boxscore"):
        path = cache_dir / f"{code}_{game}_{name}.json" if cache_dir else None
        if path is not None and path.exists():
            out[name] = json.loads(path.read_text(encoding="utf-8"))
            continue
        time.sleep(delay)
        try:
            r = requests.get(f"{BASE}/{name}", params={"gamecode": game, "seasoncode": code},
                             headers=HEADERS, timeout=30)
        except requests.RequestException:
            return None
        if r.status_code != 200 or not r.text.strip():
            return None
        try:
            out[name] = r.json()
        except ValueError:
            return None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out[name], ensure_ascii=False), encoding="utf-8")
    return out


# ─────────────────────────────────────────────────────────────────── parsing
def _clock(marker: str | None) -> float | None:
    if not marker or ":" not in str(marker):
        return None
    m, s = str(marker).split(":")[:2]
    return int(m) * 60 + float(s)


def _s(v) -> str | None:
    v = (v or "").strip() if isinstance(v, str) else v
    return v or None


def parse_events(doc: dict, competition: str, season: int, game: int) -> pd.DataFrame:
    pbp, points = doc["PlayByPlay"], doc.get("Points") or {}
    home = _s(pbp.get("CodeTeamA"))
    shots = {r["NUM_ANOT"]: r for r in points.get("Rows", [])}
    rows, last_clock, last_period = [], 600.0, 1
    # Some play-by-play files use a temporary code instead of the player's id (e.g. "PJUG"):
    # map those to the official id through the name in the box score.
    official = {}
    for side in (doc.get("Boxscore") or {}).get("Stats", []):
        for pl in side.get("PlayersStats", []):
            official[(pl["Team"].strip(), str(pl["Player"]).strip().upper())] = pl["Player_ID"].strip()
    known_ids = set(official.values())
    for key in PERIOD_KEYS:
        for r in pbp.get(key) or []:
            raw = _s(r.get("PLAYTYPE"))
            minute = int(r.get("MINUTE") or 1)
            if key != "ExtraTime":
                period = PERIOD_KEYS.index(key) + 1
            elif raw in ("EP", "EG"):            # end-of-overtime rows carry the next minute (46, 51…)
                period = last_period
            else:
                period = 5 + max(minute - 41, 0) // 5
            last_period = period
            length = 600 if period <= 4 else 300
            clock = _clock(r.get("MARKERTIME"))
            if clock is None:
                clock = length if raw == "BP" else 0.0 if raw in ("EP", "EG") else last_clock
            last_clock = clock
            team = _s(r.get("CODETEAM"))
            player = _s(r.get("PLAYER_ID"))
            if player is not None and known_ids and player not in known_ids:
                player = official.get((team, str(r.get("PLAYER") or "").strip().upper()), player)
            action = ACTION_MAP.get(raw, "other")
            if action in ("orb", "drb") and player is None:
                action = "team_" + action
            shot = shots.get(r.get("NUMBEROFPLAY"), {})
            x, y = shot.get("COORD_X"), shot.get("COORD_Y")
            if action.startswith("ft") or (x == -1 and y == -1):
                x = y = None
            rows.append({
                "source": "euroleague-api", "competition": competition, "season": season, "game_id": f"{game}",
                "period": period, "clock_s": clock, "elapsed_s": elapsed(period, clock),
                "team": team, "is_home": None if team is None else team == home,
                "player_id": player, "player_name": _s(r.get("PLAYER")), "action": action,
                "score_home": r.get("POINTS_A"), "score_away": r.get("POINTS_B"),
                "x": x, "y": y,
                "fastbreak": shot.get("FASTBREAK") == "1" if shot else None,
                "second_chance": shot.get("SECOND_CHANCE") == "1" if shot else None,
                "raw_type": raw, "raw_text": _s(r.get("PLAYINFO")),
            })
    return finish(rows)


def parse_box(doc: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Official box score: (teams, players) in made / missed format."""
    def conv(d: dict) -> dict:
        return {"fg2_made": d["FieldGoalsMade2"], "fg2_missed": d["FieldGoalsAttempted2"] - d["FieldGoalsMade2"],
                "fg3_made": d["FieldGoalsMade3"], "fg3_missed": d["FieldGoalsAttempted3"] - d["FieldGoalsMade3"],
                "ft_made": d["FreeThrowsMade"], "ft_missed": d["FreeThrowsAttempted"] - d["FreeThrowsMade"],
                "orb": d["OffensiveRebounds"], "drb": d["DefensiveRebounds"], "ast": d["Assistances"],
                "tov": d["Turnovers"], "stl": d["Steals"], "blk": d["BlocksFavour"], "pts": d["Points"]}

    teams, players = [], []
    for side in doc["Boxscore"]["Stats"]:
        code = side["PlayersStats"][0]["Team"].strip()          # the order may differ from the header
        teams.append({"team": code, **conv(side["totr"])})
        for p in side["PlayersStats"]:
            mins = str(p.get("Minutes") or "0:0")
            m, s = (mins.split(":") + ["0"])[:2] if ":" in mins else (0, 0)
            players.append({"team": code, "player_id": p["Player_ID"].strip(), "player_name": p["Player"],
                            "starter": bool(p.get("IsStarter")), "seconds": int(m) * 60 + int(s), **conv(p)})
    return pd.DataFrame(teams), pd.DataFrame(players)


def game_info(doc: dict, competition: str, season: int, game: int) -> dict:
    h = doc["Header"]
    return {"competition": competition, "season": season, "game_id": f"{game}", "date": h.get("Date"),
            "round": h.get("Round"), "home": h["CodeTeamA"].strip(), "away": h["CodeTeamB"].strip(),
            "home_name": h.get("TeamA"), "away_name": h.get("TeamB")}
