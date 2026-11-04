"""SQL over the whole warehouse with DuckDB: every league and season as one set of tables.

    python -m warehouse.sql                                   # list the tables and their rows
    python -m warehouse.sql "SELECT competition, season, count(*) FROM games GROUP BY ALL ORDER BY ALL"
    python -m warehouse.sql --file queries/home_advantage.sql
    python -m warehouse.sql --file queries/home_advantage.sql --csv results/home_advantage.csv
    python -m warehouse.sql --parquet data/parquet            # columnar copy (DuckDB, Polars, Spark…)

In Python:

    from warehouse.sql import connect
    con = connect()
    df = con.sql("SELECT * FROM team_games WHERE competition = 'wnba'").df()

Every table gets two columns taken from its file name, `competition` and `season` (starting year),
so leagues and seasons can be queried together. Tables: events, stints, possessions, games,
team_games, player_games, context, players (one per league: <league>_players.csv).
Nothing is copied: the views read the CSV files where they are. `--parquet` writes a
partitioned columnar copy (competition=…/season=…) that scales to Spark or a data lake.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

WAREHOUSE = Path("data/warehouse")
# table -> (file pattern, has the season in the file name)
TABLES = {
    "events": ("*_*_events.csv.gz", True),
    "stints": ("*_*_stints.csv.gz", True),
    "possessions": ("*_*_possessions.csv.gz", True),
    "games": ("*_*_games.csv", True),
    "team_games": ("*_*_team_games.csv", True),
    "player_games": ("*_*_player_games.csv", True),
    "context": ("*_*_context.csv", True),
    "players": ("*_players.csv", False),
}
# "games" must not swallow team_games / player_games: they share the suffix
EXCLUDE = {"games": ("_team_games.csv", "_player_games.csv")}
NAME_RE = r"([a-z0-9]+)_(\d{4})_[a-z_]+\.csv(?:\.gz)?$"


def files(table: str, base: Path = WAREHOUSE) -> list[Path]:
    pattern, _ = TABLES[table]
    out = sorted(base.glob(pattern))
    skip = EXCLUDE.get(table, ())
    return [f for f in out if not f.name.endswith(skip)]


ID_COLUMNS = ("game_id", "player_id", "team", "opponent", "home", "away", "home_on", "away_on", "offense",
              "team_id", "person_id", "match_id")


def _header(path: Path) -> list[str]:
    import csv
    import gzip
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as f:
        return next(csv.reader(f), [])


def _view_sql(table: str, paths: list[Path]) -> str:
    """One SELECT per file (ids read as text, so NBA ids keep their leading zeros), joined by name."""
    _, seasonal = TABLES[table]
    parts = []
    for path in paths:
        cols = _header(path)
        types = ", ".join(f"'{c}': 'VARCHAR'" for c in ID_COLUMNS if c in cols)
        drop = [c for c in ("competition", "season") if c in cols]     # the file name wins
        keep = f"* EXCLUDE ({', '.join(drop)})" if drop else "*"
        name = path.name
        if seasonal:
            m = re.match(NAME_RE, name)
            comp, season = (m.group(1), int(m.group(2))) if m else (name.split("_")[0], None)
            meta = f"'{comp}' AS competition, {season if season is not None else 'NULL'}::INTEGER AS season"
        else:
            meta = f"'{name.split('_')[0]}' AS competition"
        src = path.as_posix().replace("'", "''")
        opts = f", types = {{{types}}}" if types else ""
        # small tables: read every row to guess the types; the big event files: a large sample (faster)
        sample = -1 if path.suffix != ".gz" else 200_000
        parts.append(f"SELECT {keep}, {meta} FROM read_csv('{src}', header = true, sample_size = {sample}{opts})")
    return f"CREATE OR REPLACE VIEW {table} AS " + "\nUNION ALL BY NAME\n".join(parts)


def connect(base: Path | str = WAREHOUSE, database: str = ":memory:"):
    """A DuckDB connection with one view per table found in `base`."""
    import duckdb
    base = Path(base)
    con = duckdb.connect(database)
    for table in TABLES:
        paths = files(table, base)
        if paths:
            con.execute(_view_sql(table, paths))
    return con


def tables(con) -> list[tuple[str, int]]:
    names = [r[0] for r in con.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()]
    return [(n, con.execute(f"SELECT count(*) FROM {n}").fetchall()[0][0]) for n in names]


def to_parquet(con, out: Path) -> None:
    """Writes every table as Parquet, partitioned by competition and season."""
    out.mkdir(parents=True, exist_ok=True)
    for name, _ in tables(con):
        cols = {r[0] for r in con.execute(f"DESCRIBE {name}").fetchall()}
        part = [c for c in ("competition", "season") if c in cols]
        target = (out / name).as_posix()
        opts = f"FORMAT PARQUET, PARTITION_BY ({', '.join(part)}), OVERWRITE_OR_IGNORE" if part else "FORMAT PARQUET"
        con.execute(f"COPY {name} TO '{target}' ({opts})")
        print(f"  {name} -> {target}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="SQL to run (or use --file).")
    ap.add_argument("--file", help="A .sql file to run.")
    ap.add_argument("--csv", help="Save the result to this CSV file.")
    ap.add_argument("--parquet", help="Write the whole warehouse as Parquet to this folder.")
    ap.add_argument("--warehouse", default=str(WAREHOUSE))
    ap.add_argument("--rows", type=int, default=40, help="Rows to print.")
    args = ap.parse_args()
    con = connect(args.warehouse)
    if args.parquet:
        to_parquet(con, Path(args.parquet))
        return
    sql = Path(args.file).read_text(encoding="utf-8") if args.file else args.query
    if not sql:
        for name, n in tables(con):
            print(f"  {name:14} {n:>12,} rows")
        return
    df = con.sql(sql).df()
    if args.csv:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.csv, index=False)
        print(f"{len(df)} rows -> {args.csv}")
    with __import__("pandas").option_context("display.width", 200, "display.max_columns", 30):
        print(df.head(args.rows).to_string(index=False))
    if len(df) > args.rows:
        print(f"… {len(df) - args.rows} more rows", file=sys.stderr)


if __name__ == "__main__":
    main()
