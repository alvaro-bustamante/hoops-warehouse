-- Home advantage by league and season: home win %, mean margin and net rating gap per 100
-- possessions (neutral-site games left out).
WITH g AS (
    SELECT h.competition, h.season, h.game_id,
           h.pts - a.pts                                   AS margin,
           100 * h.pts / h.possessions - 100 * a.pts / a.possessions AS net100
    FROM team_games h
    JOIN team_games a
      ON a.competition = h.competition AND a.season = h.season AND a.game_id = h.game_id AND a.team <> h.team
    LEFT JOIN context c
      ON c.competition = h.competition AND c.season = h.season AND c.game_id = h.game_id AND c.team = h.team
    WHERE h.is_home AND coalesce(c.neutral, false) = false
)
SELECT competition, season, count(*) AS games,
       round(100 * avg(CASE WHEN margin > 0 THEN 1 ELSE 0 END), 1) AS home_win_pct,
       round(avg(margin), 2)                                       AS home_margin,
       round(avg(net100), 2)                                       AS home_net_per100
FROM g
GROUP BY ALL
ORDER BY competition, season;
