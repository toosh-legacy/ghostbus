# Copilot providers: offline vs LLM

OpenAI model: `gpt-4o-mini`; Ollama (opt-in, COMPARE_OLLAMA=1): `llama3.2:3b`. Temperature 0. Same validator, read-only connection, limits and timeout for both. Database: `analytics.duckdb`.

| provider | gold accuracy (40) | refusals (10) | red-team failures (25) | DB unchanged | time |
|---|---|---|---|---|---|
| offline | 100.0% | 100% | 0 | True | 2.3 s |
| openai | 95.0% | 100% | 0 | True | 71.1 s |

Interpretation: LLM mistakes show up as wrong or rejected queries, never as unsafe ones — the validator, not the model, is the security boundary.
