# Threat model (STRIDE, local Phases A/B)

| Threat | Example | Mitigation implemented | Where |
|---|---|---|---|
| Spoofing | Forged API key | Keys hashed (SHA-256) at rest, constant-time comparison, invalid key → 401 | `services/api/gbapi/keys.py` |
| Tampering | Poisoned data in the lake | SHA-256 of every download in `data/manifest.json`; raw snapshots append-only, written atomically | `gbdata/download.py`, recorder |
| Repudiation | Disputed admin action | `audit_log` table (writes from admin actions to add in Phase B; admin views are read-only today) | `gbcore/db.py` |
| Information disclosure | Copilot reading raw tables/files | sqlglot validator (single SELECT, allowlisted views, no table functions/paths/PRAGMA/ATTACH), read-only DuckDB with `enable_external_access=false`, LIMIT 500; red team 0 failures | `copilot/gbcopilot/validator.py` |
| Information disclosure | Rider location in logs | Query strings never logged, uvicorn access log off, privacy eval = 0 leaks; geolocation never stored | `gbapi/app.py`, `evals` |
| Denial of service | Expensive copilot query, SSE floods, API abuse | 5 s query timeout (interrupt), row limit, SSE subscriber cap (2000) + bounded queues, per-key/anon token-bucket rate limit (429) | validator, `gbcore/interfaces.py`, `keys.py` |
| Elevation of privilege | Admin pages exposed | `X-Admin-Token` (constant-time) on `/admin/v1/*`; Entra roles + Front Door in Phase C | `gbapi/app.py` |

Respecting the source: recorder polls each feed ≥ 15 s apart (20 s), descriptive User-Agent, If-Modified-Since, exponential backoff; raw feeds are never committed or redistributed.
