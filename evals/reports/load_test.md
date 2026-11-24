# SSE load test (Locust)

20 concurrent riders, spawn rate 10/s, 20 s, API on a 20x simulator replay (one uvicorn worker). Each rider holds one /v1/stream for 40 s.

| Measure | Count | Failures | Median (ms) | p95 (ms) | p99 (ms) | Max (ms) |
|---|---|---|---|---|---|---|
| SSE connect | 20 | 0 | 730 | 760 | 760 | 761 |
| SSE vehicle | 903 | 0 | 870 | 2700 | 4100 | 8231 |
| SSE session | 10 | 0 | 40000 | 41000 | 41000 | 40604 |
