-- Data quality of every league-season: how many games match the official box score, the final
-- score and the official minutes, and how many have reliable lineups. Empty (NULL) when a source
-- does not allow that check (e.g. no official minutes to compare with).
SELECT competition, season,
       count(*) AS games,
       round(100 * avg(CASE WHEN box_mismatches IS NULL THEN NULL WHEN box_mismatches = 0 THEN 1 ELSE 0 END), 1)
           AS pct_exact_box,
       round(100 * avg(CASE WHEN score_ok IS NULL THEN NULL WHEN score_ok THEN 1 ELSE 0 END), 1)
           AS pct_right_score,
       round(100 * avg(CASE WHEN minutes_max_gap_s IS NULL THEN NULL WHEN minutes_max_gap_s <= 60 THEN 1 ELSE 0 END), 1)
           AS pct_minutes_1min,
       round(100 * avg(CASE WHEN lineups_ok IS NULL THEN NULL WHEN lineups_ok THEN 1 ELSE 0 END), 1)
           AS pct_lineups_ok
FROM games
GROUP BY ALL
ORDER BY competition, season;
