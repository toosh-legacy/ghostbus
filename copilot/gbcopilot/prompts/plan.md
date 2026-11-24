---
name: plan
version: 2
---
Choose how to answer a reliability data question. Prefer a tool when one fits exactly; otherwise write SQL.

Tools (read-only, validated) and when to use them:
- get_route_metrics(route_id, metric, month?, day_type, time_band): ONE named route. With month = that month's
  value; without month = every month (use for "change by month" / trend questions about one route).
- compare_routes(route_ids, metric, month, day_type, time_band): TWO OR MORE named routes in ONE month.
  Put every route in route_ids.
- get_missed_trip_rates(month, order, limit, day_type, time_band): rank routes by missed-trip rate in one month
  ("which N routes had the highest/lowest missed-trip rate"). order "desc" = highest first.

action = "sql" for anything else (rankings of other metrics, counts, several months for several routes).
Route ids are strings: SL1 = "741", SL3 = "743". Months look like "2025-12"; "last month" = $last_month.
metric is one of: on_time_performance, headway_adherence, excess_wait_time_s, bunching_rate, missed_trip_rate.
day_type: all | weekday | weekend. time_band: all | am_peak | midday | pm_peak | evening | early_late.

Examples:
- "How did bunching on route 66 change by month?" → tool get_route_metrics {route_id: "66", metric: "bunching_rate"}
- "Compare on-time performance for route 1 and SL3 in November 2025" → tool compare_routes
  {route_ids: ["1", "743"], metric: "on_time_performance", month: "2025-11"}
- "Which 3 routes had the most missed trips on weekday PM peaks last month?" → tool get_missed_trip_rates
  {month: "$last_month", order: "desc", limit: 3, day_type: "weekday", time_band: "pm_peak"}
- "Which 2 routes had the lowest headway adherence in July 2025?" → sql

Question: $question
