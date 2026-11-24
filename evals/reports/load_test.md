# SSE load test (Locust)

20 concurrent riders, spawn rate 10/s, 20 s, API on a 20x simulator replay (one uvicorn worker). Each rider holds one /v1/stream for 40 s.

| Measure | Count | Failures | Median (ms) | p95 (ms) | p99 (ms) | Max (ms) |
|---|---|---|---|---|---|---|
| SSE connect | 20 | 0 | 22 | 34 | 34 | 34 |
| SSE vehicle | 476 | 0 | 1000 | 2000 | 3100 | 6204 |
| SSE session | 17 | 0 | 41000 | 41000 | 41000 | 40772 |
