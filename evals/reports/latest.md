# Evaluation report (full)

Generated 2026-11-23T22:11:01+00:00 · 12/12 gates pass

| Suite | Metric | Value | Gate | Result |
|---|---|---|---|---|
| Map matching | worst-scenario median error | 7.0 m | ≤ 25 m | PASS |
| Arrival detection | worst-scenario median / p90 | 4.7 s / 22.6 s | ≤ 20 s / ≤ 60 s | PASS |
| Ghost rules | min precision / recall of High | 1.000 / 1.000 | ≥ 0.9 / ≥ 0.9 | PASS |
| Ghost rules | Unknown during outage | 100% | 100% | PASS |
| Replay determinism | outputs 1× vs 20× (sim hour, recorded hour) | identical | identical, 0 dropped | PASS |
| Model A | p50 MAE vs best baseline | 95.6 s vs 139.9 s (propagation) | beats baseline | PASS |
| Model A | 80% interval coverage | 79.5% | 78–82% | PASS |
| Model B | Brier vs rolling baseline; PR-AUC | 0.01748 vs 0.01767; PR-AUC 0.034 vs 0.023 | beats baseline | PASS |
| Copilot accuracy | 40 gold questions | 100.0% | ≥ 90% | PASS |
| Copilot refusals | 10 unanswerable | 100% | 100% | PASS |
| Copilot red team | 25 prompts + 20 SQL payloads | 0 failures; DB unchanged=True; cross join timed out cleanly in 5.07 s | 0 | PASS |
| Privacy | coordinates in logs | 0 | 0 | PASS |

## Simulator scenarios

Config: {'hours': 2.0, 'routes': 'all 10 pilot routes'}

| scenario | trips | ghosts | map-match median / p90 (m) | arrival median / p90 (s) | arrival recall | ghost P / R | unknown at end | gates |
|---|---|---|---|---|---|---|---|---|
| normal | 184 | 6 | 6.0 / 20.3 | 4.0 / 14.3 | 0.93 | 1.000 / 1.000 | 0 | all pass |
| heavy_delays | 184 | 3 | 6.3 / 21.3 | 3.8 / 14.8 | 0.86 | 1.000 / 1.000 | 0 | all pass |
| bunching | 184 | 9 | 6.2 / 21.6 | 4.1 / 16.4 | 0.91 | 1.000 / 1.000 | 0 | all pass |
| feed_outage | 184 | 5 | 6.2 / 21.5 | 4.7 / 22.6 | 0.92 | 1.000 / 1.000 | 8 | all pass |
| many_ghosts | 184 | 45 | 6.2 / 21.1 | 4.1 / 15.2 | 0.93 | 1.000 / 1.000 | 0 | all pass |
| wrong_trip | 184 | 7 | 7.0 / 23.2 | 4.3 / 15.5 | 0.91 | 1.000 / 1.000 | 0 | all pass |

## Failures explained

- none
