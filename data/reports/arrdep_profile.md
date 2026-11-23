# MBTA Bus Arrival Departure Times — profile and data quality

Source: MBTA Bus Arrival Departure Times 2025 (CC0). Months loaded: 2025-07, 2025-08, 2025-09, 2025-10, 2025-11, 2025-12.
GROUND_TRUTH_SOURCE=real. Pilot routes (GTFS ids): 111, 743, 23, 28, 741, 1, 39, 109, 66, 77.

- Total rows (all routes, loaded months): 14,380,335
- Pilot-route rows after dedupe: 3,850,520
- Duplicate events (pilot): 0
- Impossible times |delay| > 3 h (pilot): 5
- After-midnight rows (scheduled >= 24:00 on service date, pilot): 229,683

## Null rates (all routes)

| column | null rate |
|---|---|
| half_trip_id | 0.00% |
| actual | 5.01% |
| stop_id | 0.00% |
| scheduled_headway | 2.74% |
| headway | 11.15% |

## Rows per month (pilot)

| month | rows | with actual | service days |
|---|---|---|---|
| 2025-07 | 633,985 | 590,479 | 31 |
| 2025-08 | 635,309 | 589,534 | 31 |
| 2025-09 | 632,204 | 596,042 | 30 |
| 2025-10 | 660,386 | 621,970 | 31 |
| 2025-11 | 627,664 | 597,341 | 30 |
| 2025-12 | 660,972 | 628,806 | 31 |

## Rows per route (pilot)

| GTFS route | D2 route | rows | with actual | half-trips |
|---|---|---|---|---|
| 111 | 111 | 598,086 | 571,038 | 59,810 |
| 743 | SL3 | 435,728 | 387,965 | 39,656 |
| 23 | 23 | 398,556 | 383,891 | 39,902 |
| 28 | 28 | 392,768 | 372,463 | 35,826 |
| 741 | SL1 | 384,556 | 333,932 | 42,760 |
| 39 | 39 | 335,777 | 317,451 | 35,376 |
| 1 | 01 | 335,623 | 323,241 | 37,314 |
| 66 | 66 | 327,567 | 312,192 | 36,441 |
| 109 | 109 | 323,483 | 317,417 | 34,033 |
| 77 | 77 | 318,376 | 304,582 | 31,863 |

## Completeness per route-day (threshold 70%)

Completeness = D2 half-trips with at least one observed event / GTFS scheduled trips for that date (feed version in effect from the archive).

- Route-days: 1,840; marked unknown: 0 (0.0%)

| route | days | mean completeness | unknown days |
|---|---|---|---|
| 1 | 184 | 97.1% | 0 |
| 109 | 184 | 99.4% | 0 |
| 111 | 184 | 98.6% | 0 |
| 23 | 184 | 96.8% | 0 |
| 28 | 184 | 96.9% | 0 |
| 39 | 184 | 93.7% | 0 |
| 66 | 184 | 97.1% | 0 |
| 741 | 184 | 95.9% | 0 |
| 743 | 184 | 97.8% | 0 |
| 77 | 184 | 97.0% | 0 |

## Days failing completeness on every pilot route (excluded): 0


## Static network (current feed)

- routes 10, stops 522, trips 15,886, shapes 52, stop-on-shape rows 1,235
- Stops more than 50 m off their shape: 0
