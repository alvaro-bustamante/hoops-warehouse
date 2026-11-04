-- NBA / WNBA: how much rest, back-to-backs and travel cost. Margin of each team by its rest
-- advantage over the opponent (days, capped at 3) and whether it played the night before.
WITH t AS (
    SELECT c.competition, c.season, c.game_id, c.team, c.is_home,
           least(c.rest_days, 3) - least(o.rest_days, 3) AS rest_edge,
           c.back_to_back, c.travel_km,
           tg.pts - og.pts                                AS margin
    FROM context c
    JOIN context o     ON o.competition = c.competition AND o.season = c.season AND o.game_id = c.game_id
                      AND o.team <> c.team
    JOIN team_games tg ON tg.competition = c.competition AND tg.season = c.season AND tg.game_id = c.game_id
                      AND tg.team = c.team
    JOIN team_games og ON og.competition = c.competition AND og.season = c.season AND og.game_id = c.game_id
                      AND og.team <> c.team
    WHERE c.season_type = 'Regular Season' AND coalesce(c.neutral, false) = false
)
SELECT competition, is_home, rest_edge, back_to_back,
       count(*) AS team_games, round(avg(margin), 2) AS mean_margin,
       round(avg(travel_km), 0) AS mean_travel_km
FROM t
WHERE rest_edge IS NOT NULL
GROUP BY ALL
HAVING count(*) >= 30
ORDER BY competition, is_home, rest_edge, back_to_back;
