"""NBA and WNBA play-by-play through nba_api (stats.nba.com: PlayByPlayV3, BoxScoreTraditionalV3,
BoxScoreSummary for attendance, arena, officials and national TV, CommonPlayerInfo for player bios).

    league "nba":  league id "00", 12-minute quarters, seasons written "2025-26"
    league "wnba": league id "10", 10-minute quarters, seasons written "2025" (a calendar year)
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd

from ..schema import elapsed, finish

TEAM_ID_MIN = 1610612737          # team ids: NBA 1610612737…, WNBA 1611661xxx (player ids are far lower)
LEAGUES = {"nba": {"id": "00", "quarter": 720}, "wnba": {"id": "10", "quarter": 600}}
TIMEOUT = 60
AST_RE = re.compile(r"\(([^()]+?) \d+ AST\)")
REB_RE = re.compile(r"Off:(\d+) Def:(\d+)")


def _key(name) -> str:
    """Accent- and case-insensitive name key ('Dončić' == 'Doncic')."""
    import unicodedata
    return unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower().strip()


def _call(fn, tries: int = 4, wait: float = 5.0):
    """stats.nba.com is slow and sometimes drops requests: retry with growing pauses."""
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 (timeouts, JSON errors, connection resets)
            last = exc
            time.sleep(wait * (k + 1) ** 2)
    raise RuntimeError(f"stats.nba.com did not answer after {tries} tries: {last}")


def fetch_game(game_id: str, cache_dir: Path | None = Path("data/raw/nba"), delay: float = 1.0,
               summary: bool = True) -> dict:
    """Play-by-play, box score and game summary of one game (cached on disk)."""
    import json
    from nba_api.stats.endpoints import boxscoretraditionalv3, playbyplayv3
    out = {}
    for name, call in (("pbp", lambda: playbyplayv3.PlayByPlayV3(game_id=game_id, timeout=TIMEOUT).get_data_frames()[0]),
                       ("box", lambda: boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id, timeout=TIMEOUT)
                        .get_data_frames()[0])):
        path = cache_dir / f"{game_id}_{name}.csv" if cache_dir else None
        if path is not None and path.exists():
            out[name] = pd.read_csv(path, dtype={"gameId": str})
            continue
        time.sleep(delay)
        out[name] = _call(call)
        if out[name] is None or out[name].empty:
            raise RuntimeError(f"empty {name} for game {game_id}")
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            out[name].to_csv(path, index=False)
    if summary:
        path = cache_dir / f"{game_id}_summary.json" if cache_dir else None
        if path is not None and path.exists():
            out["summary"] = json.loads(path.read_text(encoding="utf-8"))
        else:
            time.sleep(delay)
            try:
                out["summary"] = _call(lambda: _summary(game_id), tries=2)
            except Exception:  # noqa: BLE001  the summary is optional: the game is still built
                out["summary"] = None
            if path is not None and out["summary"] is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(out["summary"]), encoding="utf-8")
    return out


def _summary(game_id: str) -> dict:
    """Raw game summary: BoxScoreSummaryV3 when the installed nba_api has it, else V2."""
    from nba_api.stats import endpoints as E
    if hasattr(E, "boxscoresummaryv3"):
        try:
            d = E.boxscoresummaryv3.BoxScoreSummaryV3(game_id=game_id, timeout=TIMEOUT).get_dict()
            return {"v": 3, "data": d}
        except Exception:  # noqa: BLE001
            pass
    d = E.boxscoresummaryv2.BoxScoreSummaryV2(game_id=game_id, timeout=TIMEOUT).get_dict()
    return {"v": 2, "data": d}


def game_context(doc: dict) -> dict:
    """Attendance, arena, tip-off, officials and national TV from the summary and the play-by-play."""
    out = {"attendance": None, "sellout": None, "arena": None, "arena_city": None, "arena_state": None,
           "arena_tz": None, "tip_et": None, "tip_utc": None, "neutral_site": None, "officials": None,
           "national_tv": None}
    pbp = doc.get("pbp")
    if pbp is not None and len(pbp):
        start = pbp[(pbp["actionType"] == "period") & (pbp["period"] == 1)]["description"].astype(str)
        m = re.search(r"\((\d{1,2}:\d{2} [AP]M) ([A-Z]{2,4})\)", " ".join(start))
        if m:
            out["tip_et"] = f"{m.group(1)} {m.group(2)}"
    s = doc.get("summary")
    if not s:
        return out
    d = s.get("data") or {}
    if s.get("v") == 3:
        b = d.get("boxScoreSummary") or d
        arena = b.get("arena") or {}
        out.update({"attendance": b.get("attendance"), "sellout": b.get("sellout"),
                    "arena": arena.get("arenaName"), "arena_city": arena.get("arenaCity"),
                    "arena_state": arena.get("arenaState"), "arena_tz": arena.get("arenaTimezone"),
                    "officials": "|".join(f"{o.get('firstName', '')} {o.get('familyName', '')}".strip()
                                          for o in b.get("officials") or []) or None})
        tv = [x.get("broadcasterDisplay") or x.get("broadcastDisplay") or x.get("broadcasterAbbreviation")
              for x in (b.get("broadcasters") or {}).get("nationalBroadcasters", []) if isinstance(x, dict)] \
            if isinstance(b.get("broadcasters"), dict) else []
        out["national_tv"] = "|".join(t for t in tv if t) or None
        out["neutral_site"] = b.get("isNeutral")
        out["tip_utc"] = b.get("gameTimeUTC")
        return out
    sets = {r["name"]: r for r in d.get("resultSets", [])}

    def rows(name):
        r = sets.get(name)
        return [dict(zip(r["headers"], row)) for row in r["rowSet"]] if r else []
    info = rows("GameInfo")
    if info:
        out["attendance"] = info[0].get("ATTENDANCE")
    gs = rows("GameSummary")
    if gs:
        out["national_tv"] = gs[0].get("NATL_TV_BROADCASTER_ABBREVIATION")
    off = rows("Officials")
    if off:
        out["officials"] = "|".join(f"{o.get('FIRST_NAME', '')} {o.get('LAST_NAME', '')}".strip() for o in off)
    return out


def season_games(season: str = "2025-26", season_type: str = "Regular Season", league: str = "nba") -> pd.DataFrame:
    from nba_api.stats.endpoints import leaguegamefinder
    g = _call(lambda: leaguegamefinder.LeagueGameFinder(season_nullable=season, league_id_nullable=LEAGUES[league]["id"],
                                                         season_type_nullable=season_type,
                                                         timeout=TIMEOUT).get_data_frames()[0])
    if g.empty:
        return g
    return g.drop_duplicates("GAME_ID").sort_values("GAME_DATE")


def player_bios(ids: list[str], league: str = "nba", cache: Path = Path("data/raw/bios"), delay: float = 0.8) -> pd.DataFrame:
    """Date of birth, height, position, first season, draft and country of every player (cached)."""
    from nba_api.stats.endpoints import commonplayerinfo
    cache = cache / league
    cache.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, pid in enumerate(sorted(set(map(str, ids)))):
        path = cache / f"{pid}.csv"
        if path.exists():
            df = pd.read_csv(path, dtype={"PERSON_ID": str})
        else:
            time.sleep(delay)
            try:
                df = _call(lambda: commonplayerinfo.CommonPlayerInfo(
                    player_id=pid, league_id_nullable=LEAGUES[league]["id"], timeout=TIMEOUT).get_data_frames()[0], tries=3)
            except Exception as exc:  # noqa: BLE001
                print(f"  bio {pid}: {exc}")
                continue
            df.to_csv(path, index=False)
        if len(df):
            rows.append(df.iloc[0].to_dict())
        if (i + 1) % 100 == 0:
            print(f"  bios: {i + 1}/{len(set(ids))}", flush=True)
    b = pd.DataFrame(rows)
    if b.empty:
        return b
    keep = {"PERSON_ID": "player_id", "DISPLAY_FIRST_LAST": "player_name", "BIRTHDATE": "birthdate", "HEIGHT": "height",
            "WEIGHT": "weight", "POSITION": "position", "COUNTRY": "country", "SCHOOL": "school",
            "FROM_YEAR": "first_season", "TO_YEAR": "last_season", "SEASON_EXP": "seasons_experience",
            "DRAFT_YEAR": "draft_year", "DRAFT_ROUND": "draft_round", "DRAFT_NUMBER": "draft_number"}
    b = b[[c for c in keep if c in b]].rename(columns=keep)
    b["player_id"] = b["player_id"].astype(str)
    b["birthdate"] = pd.to_datetime(b["birthdate"], errors="coerce").dt.date
    if "height" in b:                                # "6-2" (feet-inches) -> cm
        hi = b["height"].astype(str).str.extract(r"(\d+)-(\d+)").astype(float)
        b["height_cm"] = ((hi[0] * 12 + hi[1]) * 2.54).round(1)
    return b


def _clock(v) -> float:
    m = re.match(r"PT(\d+)M([\d.]+)S", str(v))
    return int(m.group(1)) * 60 + float(m.group(2)) if m else 0.0


def roster_from_box(box: pd.DataFrame | None) -> pd.DataFrame | None:
    if box is None or box.empty:
        return None
    return pd.DataFrame({"team": box["teamTricode"], "player_id": box["personId"].astype(str),
                         "name": box["familyName"], "full": box["firstName"] + " " + box["familyName"]})


def parse_events(doc: dict, season: str, game_id: str, league: str = "nba") -> pd.DataFrame:
    quarter = LEAGUES[league]["quarter"]
    pbp = doc["pbp"].copy()
    pbp = pbp.sort_values("actionNumber")
    side = {loc: t for loc, t in pbp.dropna(subset=["teamTricode"]).groupby("location")["teamTricode"].first().items()}
    home, away = side.get("h"), side.get("v")

    # name -> id within each team. Texts write players as "Thomas", "J. Thomas" or "Jasmine Thomas";
    # a surname alone is only trusted when no teammate shares it.
    names: dict[tuple, str] = {}
    surnames: dict[tuple, set] = {}
    roster = roster_from_box(doc.get("box"))
    box_ids: dict[str, set] = {}
    if roster is not None:
        for r in roster.itertuples():
            first = str(r.full).split()[0] if str(r.full).split() else ""
            names[(r.team, _key(r.full))] = r.player_id
            names[(r.team, _key(f"{first[:1]}. {r.name}"))] = r.player_id
            surnames.setdefault((r.team, _key(r.name)), set()).add(r.player_id)
            box_ids.setdefault(r.team, set()).add(r.player_id)
    for r in pbp[(pbp.personId > 0) & (pbp.personId < TEAM_ID_MIN)].itertuples():
        if isinstance(getattr(r, "playerNameI", None), str):
            names.setdefault((r.teamTricode, _key(r.playerNameI)), str(r.personId))
        surnames.setdefault((r.teamTricode, _key(r.playerName)), set()).add(str(r.personId))
    for k, ids in surnames.items():
        if len(ids) == 1:
            names.setdefault(k, next(iter(ids)))
    # the player leaving in "SUB: X FOR Y" is the row's own player: this teaches the name the texts use
    # for her (useful when she changed surname, e.g. "Little" for Camille Smith)
    for r in pbp[(pbp.actionType == "Substitution") & (pbp.personId > 0) & (pbp.personId < TEAM_ID_MIN)].itertuples():
        m = re.match(r"SUB:\s*(.+?)\s+FOR\s+(.+)$", str(r.description or ""))
        if m:
            names.setdefault((r.teamTricode, _key(m.group(2).strip())), str(r.personId))
    # hyphenated surnames written in part ("Boyd" for Brittany Boyd-Jones)
    if roster is not None:
        for r in roster.itertuples():
            for part in re.split(r"[-\s]", str(r.name)):
                if len(part) > 2 and len(surnames.get((r.team, _key(part)), ())) <= 1:
                    names.setdefault((r.team, _key(part)), r.player_id)
    # names written in the texts that match nobody (e.g. a player listed under a former surname):
    # if exactly one player of that team is never named, it is her
    texts = []
    for r in pbp.itertuples():
        d = str(r.description or "")
        m = AST_RE.search(d)
        if m:
            texts.append((r.teamTricode, m.group(1)))
        m = re.match(r"SUB:\s*(.+?)\s+FOR\s+(.+)$", d)
        if m:
            texts.append((r.teamTricode, m.group(1)))
    unresolved: dict[str, set] = {}
    named: dict[str, set] = {}
    for team, nm in texts:
        pid = names.get((team, _key(nm.strip())))
        (named.setdefault(team, set()).add(pid) if pid else unresolved.setdefault(team, set()).add(nm.strip()))
    acting = {(t, str(i)) for t, i in zip(pbp["teamTricode"], pbp["personId"]) if 0 < i < TEAM_ID_MIN}
    for team, nms in unresolved.items():
        never = {i for i in box_ids.get(team, set()) if i not in named.get(team, set()) and (team, i) not in acting}
        never |= {i for (t, i) in acting if t == team and i not in named.get(team, set())}
        if len(nms) == 1 and len(never) == 1:
            names[(team, _key(next(iter(nms))))] = next(iter(never))

    def by_name(team, name):
        name = (name or "").strip()
        return names.get((team, _key(name)), f"name:{name}")

    rows, last_miss_team, reb_counts = [], None, {}
    for r in pbp.itertuples():
        period = int(r.period)
        clock = _clock(r.clock)
        base = {"source": "nba-stats", "competition": league, "season": season, "game_id": str(game_id),
                "period": period, "clock_s": clock, "elapsed_s": elapsed(period, clock, quarter, 300),
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
            m = REB_RE.search(desc)
            if m and player is not None:                  # the text keeps each player's running counts
                prev = reb_counts.get(player, (0, 0))
                cur = (int(m.group(1)), int(m.group(2)))
                if cur != prev:
                    offensive = cur[0] > prev[0]
                reb_counts[player] = cur
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
    ev = finish(rows)
    for c in ("score_home", "score_away"):          # some feeds write 0 on the last rows: a score never goes down
        ev[c] = ev[c].cummax()
    return ev


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
