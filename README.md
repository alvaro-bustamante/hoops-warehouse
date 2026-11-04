# Hoops Warehouse

**EN** · A reproducible play-by-play warehouse for basketball research: official play-by-play from
the EuroLeague, EuroCup and NBA turned into one common event format, with **who is on court at
every moment**, **lineup stints** and **possessions**, checked game by game against the official
box score.

**ES** · Un almacén reproducible de jugada a jugada para investigación en baloncesto: el
play-by-play oficial de EuroLeague, EuroCup y NBA en un formato común de sucesos, con **quién está
en pista en cada momento**, **tramos de quinteto** y **posesiones**, validado partido a partido contra
el box score oficial.

---

## Why / Por qué

**EN** · Lineup, possession and passing-network analyses depend on reconstructions that are rarely
checked. Every game built here is validated: event totals must match the official box score
exactly, the final score must match, and minutes rebuilt from substitutions must match the
official minutes.

**ES** · Los análisis de quintetos, posesiones y redes de pase dependen de reconstrucciones que casi
nunca se comprueban. Cada partido se valida: los totales deben coincidir exactamente con el box
score oficial, el marcador final también, y los minutos reconstruidos a partir de los cambios deben
coincidir con los minutos oficiales.

| Share of games that pass (2016–2025) / Partidos que pasan | EuroLeague | EuroCup | NBA | WNBA |
|---|---|---|---|---|
| Final score / Marcador final | 100% | 100% | 99–100% | 99–100% |
| Player box score, every stat / Box score por jugador, todo | 100% | 100% | 89–95% | 94–99% |
| Minutes per player within 1 min / Minutos por jugador a < 1 min | 98–100% | 94–99% | 63–80% | 68–92% |

**EN** · Scores and box scores come straight from the events. Minutes depend on who is on court,
which is inferred (the NBA and WNBA feeds miss some substitutions, mostly between quarters), so
lineup and on/off analyses should keep the games flagged `lineups_ok` or filter on
`minutes_max_gap_s`; improving that inference is the next item of the roadmap.
**ES** · Marcador y box score salen directamente de los sucesos. Los minutos dependen de quién está
en pista, que se infiere: para análisis de quintetos conviene filtrar por `lineups_ok` o
`minutes_max_gap_s`.

Each game's checks are kept in `<comp>_<season>_games.csv`, so every analysis can filter on them
(`python -m warehouse.sql --file queries/data_quality.sql`). Coverage today: EuroLeague and EuroCup
2016-17 → today, NBA 2015-16 → today (~14,000 games, regular season and playoffs) and WNBA
2015 → 2025 (2,531 games).

## Event format / Formato de sucesos

**EN** · One row per event. **Actions are mutually exclusive**: a shot is either made or missed,
never "attempted"; attempts are derived only when a metric needs them.

**ES** · Una fila por suceso. **Las acciones son mutuamente excluyentes**: un tiro es anotado o
fallado, nunca "intentado"; los intentos solo se calculan cuando una métrica los necesita.

| Group | Actions |
|---|---|
| Shots / Tiros | `fg2_made`, `fg2_missed`, `fg3_made`, `fg3_missed`, `ft_made`, `ft_missed` |
| Rebounds / Rebotes | `orb`, `drb`, `team_orb`, `team_drb` |
| Ball / Balón | `ast`, `tov`, `stl`, `blk`, `blk_against` |
| Fouls / Faltas | `foul`, `foul_offensive`, `foul_technical`, `foul_unsportsmanlike`, `foul_drawn` |
| Lineups / Quintetos | `sub_in`, `sub_out` |
| Game / Partido | `jump_ball`, `timeout`, `period_start`, `period_end`, `game_end`, `other` |

Columns include period, clock and elapsed seconds, team, player, score, shot location (source
units), fast-break and second-chance flags (EuroLeague), the raw source code, and after the build
`home_on` / `away_on` (the five player ids on court), `possession_id` and `offense`.

## Tables / Tablas

| File | One row per… / Una fila por… |
|---|---|
| `<comp>_<season>_events.csv.gz` | event / suceso |
| `<comp>_<season>_stints.csv.gz` | lineup stint, 5 vs 5 (seconds, points, possessions) / tramo de quinteto |
| `<comp>_<season>_possessions.csv.gz` | possession (team, start, end, points, how it ended) / posesión |
| `<comp>_<season>_games.csv` | game, with every check / partido, con todas las comprobaciones |
| `<comp>_<season>_team_games.csv` | team and game: box score, possessions / equipo y partido |
| `<comp>_<season>_player_games.csv` | player and game: box score, seconds, +/-, possessions on court / jugador y partido |

**EN** · The two game tables are the layer every app reads (the 2D Explorer, the league translator and
the scouting reports). Competitions without play-by-play write them directly from box scores
(`level = "box"`). `python -m warehouse.gametables` rebuilds them for every built season.
**ES** · Las dos tablas por partido son la capa que leen todas las apps. Las competiciones sin jugada a
jugada las escriben directamente desde el box score (`level = "box"`).

## Method / Método

- **Lineups / Quintetos** · The five players of each team at the start of each period are inferred
  from the events (a player whose first appearance is not an entry started the period); players
  who start a period without any event are carried over. Substitutions then update the lineup.
  Stints are timed from the moment of the substitution.
- **Possessions / Posesiones** · A possession changes when the other team does something only the
  team with the ball can do (shoot, defensive rebound, turnover…). Offensive rebounds keep the
  possession; and-one free throws stay with the made shot; technical free throws do not change it.
  The event count is reported next to the classic estimate FGA + 0.44·FTA − ORB + TOV.

## Use / Uso

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   ·   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m warehouse.build euroleague --season 2025          # EuroLeague 2025-26, every played game
python -m warehouse.build eurocup --season 2025 --games 1-20
python -m warehouse.build nba --season 2025-26 --limit 50
python -m warehouse.gametables                               # team / player game tables of every built season
python -m warehouse.history wnba --from 2015 --to 2025       # many WNBA (or NBA) seasons + context + player bios
python -m warehouse.context wnba_2025                        # rest, travel, time zones, crowd, TV of each team-game
python -m pytest tests                                       # checks on the downloaded games
```

Downloads are cached in `data/raw/`; a second run reads the disk only. `data/` is never committed.
Las descargas se guardan en `data/raw/`; una segunda ejecución solo lee el disco. `data/` nunca se sube.

## SQL over every league / SQL sobre todas las ligas (DuckDB)

**EN** · `warehouse.sql` exposes every table of every league and season as one DuckDB view
(`events`, `stints`, `possessions`, `games`, `team_games`, `player_games`, `context`, `players`), with
`competition` and `season` taken from the file names. Nothing is copied; `--parquet` writes a
partitioned columnar copy (`competition=…/season=…`) ready for Polars, Spark or a data lake.
**ES** · `warehouse.sql` expone todas las tablas de todas las ligas y temporadas como vistas de DuckDB,
sin copiar nada; `--parquet` genera una copia columnar particionada.

```bash
python -m warehouse.sql                                        # tables and rows
python -m warehouse.sql --file queries/data_quality.sql        # checks of every league-season
python -m warehouse.sql --file queries/home_advantage.sql      # home win %, margin and net rating by season
python -m warehouse.sql --file queries/rest_and_travel.sql     # NBA / WNBA: rest edge, back-to-backs, travel
python -m warehouse.sql --file queries/top_lineups.sql         # best five-man units of a season
python -m warehouse.sql "SELECT competition, count(*) FROM possessions GROUP BY ALL"
python -m warehouse.sql --parquet data/parquet
```

```python
from warehouse.sql import connect
con = connect()
df = con.sql("SELECT * FROM team_games JOIN context USING (competition, season, game_id, team)").df()
```

## Docker

```bash
docker build -t hoops-warehouse .
docker run --rm -v "$PWD/data:/app/data" hoops-warehouse                     # list the tables
docker run --rm -v "$PWD/data:/app/data" hoops-warehouse \
       python -m warehouse.sql --file queries/home_advantage.sql
docker run --rm hoops-warehouse python -m pytest -q                            # tests
```

The image holds the code only; the data folder is mounted from the host.
La imagen solo contiene el código; la carpeta de datos se monta desde fuera.

## Tests

`tests/test_core.py` checks the core on a synthetic game (clock, box score, possessions with
offensive rebounds and and-ones, lineups, stints and minutes, the validation report, travel distance
and time zones); `tests/test_sql.py` checks the DuckDB layer; `tests/test_games.py` is a data audit that rebuilds every EuroLeague /
EuroCup game in `data/raw/` and checks the share that match the official sheets (it runs only with
`HW_DATA_TESTS=1`, as it takes about half an hour). The unit tests run on every
push (GitHub Actions, Python 3.11–3.13, and inside the Docker image).

## Data / Datos

Sources: EuroLeague/EuroCup official live API (`live.euroleague.net`) and NBA Stats through
[`nba_api`](https://github.com/swar/nba_api). This repository contains code only, no data.
Data belong to their owners; check each source's terms before redistributing derived tables.

## Roadmap

- [x] v0 · Events, lineups, stints, possessions, validation (EuroLeague, EuroCup, NBA)
- [x] v0.2 · Team and player game tables (minutes, +/-, possessions on court), `lineups_ok` flag
- [x] Assist pairing (passer → scorer) / Emparejamiento de asistencias
- [x] WNBA and NBA through the same reader (10- and 12-minute quarters), with attendance, arena, officials and
      national TV per game, game context (rest, travel distance, time zones and direction, road trips, crowd,
      the 2020 bubbles) and player bios / WNBA y NBA con contexto de cada partido y biografía de jugadoras
- [x] SQL layer (DuckDB, Parquet export), Docker image, tests and CI / Capa SQL, Docker, tests e integración continua
- [ ] Better lineup inference for the NBA / WNBA feeds (missing substitutions) / Mejor inferencia de quintetos
- [ ] Scheduled updates / Actualizaciones programadas
- [ ] Bayesian lineup ratings (RAPM-lite) / Valoración bayesiana de quintetos

## Author / Autor

**Álvaro Bustamante-Sánchez, PhD** · Head of the Applied Technology & Research Lab ·
[LinkedIn](https://www.linkedin.com/in/alvaro-bustamante-163b5655) ·
[Google Scholar](https://scholar.google.com/citations?user=e_gK-2YAAAAJ)

MIT License.
