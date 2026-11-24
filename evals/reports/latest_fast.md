# Evaluation report (fast CI subset)

Generated 2026-11-24T06:52:56+00:00 · 14/14 gates pass

| Suite | Metric | Value | Gate | Result |
|---|---|---|---|---|
| Map matching | worst-scenario median error | 7.6 m | ≤ 25 m | PASS |
| Arrival detection | worst-scenario median / p90 | 5.7 s / 34.5 s | ≤ 20 s / ≤ 60 s | PASS |
| Ghost rules | min precision / recall of High | 1.000 / 1.000 | ≥ 0.9 / ≥ 0.9 | PASS |
| Ghost rules | Unknown during outage | 100% | 100% | PASS |
| Replay determinism | outputs 1× vs 20× (sim hour, recorded hour) | identical | identical, 0 dropped | PASS |
| Model A | p50 MAE vs best baseline | 94.0 s vs 139.9 s (propagation) | beats baseline | PASS |
| Model A | 80% interval coverage | 79.4% | 78–82% | PASS |
| Model B | Brier vs rolling baseline; PR-AUC | 0.01758 vs 0.01767; PR-AUC 0.025 vs 0.023 | beats baseline | PASS |
| Copilot accuracy | 40 gold questions | 100.0% | ≥ 90% | PASS |
| Copilot refusals | 10 unanswerable | 100% | 100% | PASS |
| Methodology RAG | recall@3 / MRR@10 (32 questions, rerank: cross_encoder) | 0.938 / 0.858 | recall@3 ≥ 0.85 | PASS |
| Methodology RAG | answers citing a gold passage | 84% | ≥ 80% | PASS |
| Copilot red team | 25 prompts + 20 SQL payloads + 9 MCP tool attacks | 0 failures; DB unchanged=True; cross join timed out cleanly in 5.06 s | 0 | PASS |
| Privacy | coordinates in logs | 0 | 0 | PASS |

## Simulator scenarios

Config: {'hours': 1.5, 'route_ids': ['1', '111']}

| scenario | trips | ghosts | map-match median / p90 (m) | arrival median / p90 (s) | arrival recall | ghost P / R | unknown at end | gates |
|---|---|---|---|---|---|---|---|---|
| normal | 32 | 1 | 5.8 / 20.1 | 4.2 / 14.9 | 0.93 | 1.000 / 1.000 | 0 | all pass |
| heavy_delays | 32 | 0 | 6.4 / 22.5 | 4.9 / 14.6 | 0.85 | 1.000 / 1.000 | 0 | all pass |
| bunching | 32 | 3 | 5.9 / 21.5 | 3.8 / 14.3 | 0.93 | 1.000 / 1.000 | 0 | all pass |
| feed_outage | 32 | 0 | 5.4 / 18.3 | 5.7 / 34.5 | 0.94 | 1.000 / 1.000 | 2 | all pass |
| many_ghosts | 32 | 4 | 6.5 / 21.5 | 4.5 / 15.7 | 0.95 | 1.000 / 1.000 | 0 | all pass |
| wrong_trip | 32 | 3 | 7.6 / 24.2 | 4.0 / 16.2 | 0.86 | 1.000 / 1.000 | 0 | all pass |

## Failures explained

- none
