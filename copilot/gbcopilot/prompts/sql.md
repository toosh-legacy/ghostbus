---
name: sql
version: 2
---
Write one DuckDB SELECT that answers a question about MBTA bus reliability. Output only SQL.

Tables:
$schema

Rules:
- Prefer v_route_timeband_metrics. Always filter day_type and time_band ('all' unless the question names one;
  time_band: am_peak, midday, pm_peak, evening, early_late; day_type: weekday, weekend).
- Select route_id and month (when the table has them) plus the metric column, with exact column names.
- Rates are fractions 0-1. Months are strings like '2025-12'; data covers $first_month to $last_month, and
  "last month" = $last_month. Every one of those months is in the past.
- Route ids are strings; SL1 = '741', SL3 = '743'.
- "Highest"/"worst" missed trips, bunching or excess wait → ORDER BY metric DESC; for on-time performance or
  headway adherence "worst" = lowest (ASC). Add LIMIT N when the question asks for N routes.
- "Change by month" → every month, ORDER BY month.
- v_missed_trip_daily is per service day (its rate column is missed_rate); for monthly missed-trip rates use
  v_route_timeband_metrics.missed_trip_rate.

Examples:
Q: What was the excess wait time of route 66 in the PM peak in August 2025?
SQL: SELECT route_id, month, excess_wait_time_s FROM v_route_timeband_metrics WHERE route_id = '66' AND month = '2025-08' AND day_type = 'all' AND time_band = 'pm_peak'
Q: Which 3 routes had the lowest on-time performance on weekdays in July 2025?
SQL: SELECT route_id, month, on_time_performance FROM v_route_timeband_metrics WHERE month = '2025-07' AND day_type = 'weekday' AND time_band = 'all' ORDER BY on_time_performance ASC LIMIT 3
$feedback
Question: $question
SQL:
