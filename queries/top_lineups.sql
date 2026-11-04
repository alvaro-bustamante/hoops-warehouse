-- Best five-man units of a league-season by net rating per 100 possessions (at least 150
-- possessions together), from the lineup stints. Change the WHERE line for another league.
WITH s AS (
    SELECT competition, season, home AS team, home_on AS lineup,
           pts_home AS pf, pts_away AS pa, poss_home AS poss_f, poss_away AS poss_a, seconds
    FROM stints
    UNION ALL
    SELECT competition, season, away, away_on, pts_away, pts_home, poss_away, poss_home, seconds
    FROM stints
)
SELECT competition, season, team, lineup,
       round(sum(seconds) / 60, 0)                           AS minutes,
       sum(poss_f) + sum(poss_a)                             AS possessions,
       round(100 * sum(pf) / sum(poss_f), 1)                 AS ortg,
       round(100 * sum(pa) / sum(poss_a), 1)                 AS drtg,
       round(100 * sum(pf) / sum(poss_f) - 100 * sum(pa) / sum(poss_a), 1) AS net
FROM s
WHERE competition = 'euroleague' AND season = 2025
GROUP BY ALL
HAVING sum(poss_f) + sum(poss_a) >= 300
ORDER BY net DESC
LIMIT 15;
