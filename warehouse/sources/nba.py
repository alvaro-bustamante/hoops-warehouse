"""NBA play-by-play through nba_api (stats.nba.com, PlayByPlayV3 + BoxScoreTraditionalV3)."""
from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd

from ..schema import elapsed, finish

TEAM_ID_MIN = 1610612737          # NBA team ids are 1610612737…1610612766
AST_RE = re.compile(r"\(([^()]+?) \d+ AST\)")


def _key(name) -> str:
    """Accent- and case-insensitive name key ('Dončić' == 'Doncic')."""
    import unicodedata
    return unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower().strip()


def fetch_game(game_id: str, cache_dir: Path | None = Path("data/raw/nba"), delay: float = 1.0) -> dict:
    """Play-by-play and box score of one game (cached as CSV)."""
    from nba_api.stats.endpoints import boxscoretraditionalv3, playbyplayv3
    out = {}
    for name, call in (("pbp", lambda: playbyplayv3.PlayByPlayV3(game_id=game_id).get_data_frames()[0]),
                       ("box", lambda: boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id)
                        .get_data_frames()[0])):
        path = cache_dir / f"{game_id}_{name}.csv" if cache_dir else None
        if path is not None and path.exists():
            out[name] = pd.read_csv(path, dtype={"gameId": str})
            continue
        time.sleep(delay)
        out[name] = call()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            out[name].to_csv(path, index=False)
    return out


def season_games(season: str = "2025-26", season_type: str = "Regular Season") -> pd.DataFrame:
    from nba_api.stats.endpoints import leaguegamefinder
    g = leaguegamefinder.LeagueGameFinder(season_nullable=season, league_id_nullable="00",
                                          season_type_nullable=season_type).get_data_frames()[0]
    return g.drop_duplicates("GAME_ID").sort_values("GAME_DATE")


def _clock(v) -> float:
    m = re.match(r"PT(\d+)M([\d.]+)S", str(v))
    return int(m.group(1)) * 60 + float(m.group(2)) if m else 0.0


def roster_from_box(box: pd.DataFrame | None) -> pd.DataFrame | None:
    if box is None or box.empty:
        return None
    return pd.DataFrame({"team": box["teamTricode"], "player_id": box["personId"].astype(str),
                         "name": box["familyName"], "full": box["firstName"] + " " + box["familyName"]})


def parse_events(doc: dict, season: str, game_id: str) -> pd.DataFrame:
    pbp = doc["pbp"].copy()
    pbp = pbp.sort_values("actionNumber")
    side = {loc: t for loc, t in pbp.dropna(subset=["teamTricode"]).groupby("location")["teamTricode"].first().items()}
    home, away = side.get("h"), side.get("v")

    # name -> id within each team (box score first, then names seen in the play-by-play)
    names: dict[tuple, str] = {}
    roster = roster_from_box(doc.get("box"))
    if roster is not None:
        for r in roster.itertuples():
            names[(r.team, _key(r.name))] = r.player_id
            names[(r.team, _key(r.full))] = r.player_id
    for r in pbp[(pbp.personId > 0) & (pbp.personId < TEAM_ID_MIN)].itertuples():
        names.setdefault((r.teamTricode, _key(r.playerName)), str(r.personId))

    def by_name(team, name):
        name = (name or "").strip()
        return names.get((team, _key(name)), f"name:{name}")

    rows, last_miss_team = [], None
    for r in pbp.itertuples():
        period = int(r.period)
        clock = _clock(r.clock)
        base = {"source": "nba-stats", "competition": "nba", "season": season, "game_id": str(game_id),
                "period": period, "clock_s": clock, "elapsed_s": elapsed(period, clock, 720, 300),
                "score_home": pd.to_numeric(r.scoreHome, errors="coerce"),
                "score_away": pd.to_numeric(r.scoreAway, errors="coerce"),
                "raw_type": f"{r.actionType}|{r.subType}", "raw_text": r.description}
        is_team_id = r.personId >= TEAM_ID_MIN
        team = r.teamTricode if isinstance(r.teamTricode, str) else side.get(r.location)
        player = None if (r.personId == 0 or is_team_id) else str(r.personId)
        pname = None if player is None else r.playerNameI
        at, sub, desc = str(r.actionType or ""), str(r.subType or ""), str(r.description or "")
        actor = {"team": team, "is_home": None if team is None else team == home, "player_id": player,
                 "player_name": pname}

        if at in ("Made Shot", "Missed Shot"):
            value = 3 if (r.shotValue == 3 or "3PT" in desc) else 2
            made = at == "Made Shot"
            rows.append({**base, **actor, "action": f"fg{value}_{'made' if made else 'missed'}",
                         "x": r.xLegacy, "y": r.yLegacy})
            last_miss_team = None if made else team
            m = AST_RE.search(desc) if made else None
            if m:
                pid = by_name(team, m.group(1))
                rows.append({**base, **actor, "action": "ast", "player_id": pid, "player_name": m.group(1),
                             "score_home": None, "score_away": None})
        elif at == "Free Throw":
            made = not desc.upper().startswith("MISS")
            rows.append({**base, **actor, "action": "ft_made" if made else "ft_missed"})
            last_miss_team = None if made else team
        elif at == "Rebound":
            offensive = last_miss_team is not None and team == last_miss_team
            kind = "orb" if offensive else "drb"
            rows.append({**base, **actor, "action": ("team_" + kind) if player is None else kind})
            last_miss_team = None
        elif at == "Turnover":
            rows.append({**base, **actor, "action": "tov"})
        elif at == "" or at == "nan":
            if "STEAL" in desc:
                rows.append({**base, **actor, "action": "stl"})
            elif "BLOCK" in desc:
                rows.append({**base, **actor, "action": "blk"})
            else:
                rows.append({**base, **actor, "action": "other"})
        elif at == "Foul":
            kind = ("foul_offensive" if sub.startswith("Offensive") else "foul_technical" if "Technical" in sub
                    else "foul_unsportsmanlike" if "Flagrant" in sub else "foul")
            rows.append({**base, **actor, "action": kind})
        elif at == "Substitution":
            m = re.match(r"SUB:\s*(.+?)\s+FOR\s+(.+)$", desc)
            rows.append({**base, **actor, "action": "sub_out"})
            if m:
                rows.append({**base, **actor, "action": "sub_in", "player_id": by_name(team, m.group(1)),
                             "player_name": m.group(1)})
        elif at == "period":
            rows.append({**base, "action": "period_start" if sub == "start" else "period_end"})
        elif at == "Jump Ball":
            rows.append({**base, **actor, "action": "jump_ball"})
        elif at == "Timeout":
            rows.append({**base, **actor, "action": "timeout"})
        else:                               # Heave, Violation, Instant Replay…
            rows.append({**base, **actor, "action": "other"})
    rows.append({**rows[-1], "action": "game_end", "team": None, "is_home": None, "player_id": None,
                 "player_name": None, "raw_type": "game_end", "raw_text": ""})
    return finish(rows)


def parse_box(doc: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    box = doc["box"]
    p = pd.DataFrame({
        "team": box["teamTricode"], "player_id": box["personId"].astype(str),
        "player_name": box["firstName"] + " " + box["familyName"],
        "fg2_made": box["fieldGoalsMade"] - box["threePointersMade"],
        "fg2_missed": (box["fieldGoalsAttempted"] - box["threePointersAttempted"])
                      - (box["fieldGoalsMade"] - box["threePointersMade"]),
        "fg3_made": box["threePointersMade"],
        "fg3_missed": box["threePointersAttempted"] - box["threePointersMade"],
        "ft_made": box["freeThrowsMade"], "ft_missed": box["freeThrowsAttempted"] - box["freeThrowsMade"],
        "orb": box["reboundsOffensive"], "drb": box["reboundsDefensive"], "ast": box["assists"],
        "tov": box["turnovers"], "stl": box["steals"], "blk": box["blocks"], "pts": box["points"],
    })
    if "minutes" in box:                       # "34:12"
        mins = box["minutes"].fillna("0:00").astype(str).str.extract(r"(\d+):(\d+)").fillna(0).astype(int)
        p["seconds"] = mins[0] * 60 + mins[1]
    t = p.drop(columns=["player_id", "player_name"]).groupby("team").sum().reset_index()
    return t, p
