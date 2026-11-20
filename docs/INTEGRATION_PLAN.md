# RELAY integration plan

This document tracks the sponsor integration boundary. SIE and Senso now have real standard-library clients with explicit provider status and offline contract tests. A live integration is only reported when credentials and a successful provider call are present. Firedrill remains an OAuth/MCP/runner operation and is not claimed as connected in this repository because no authorized connection is configured.

| Sponsor | Planned responsibility | Existing RELAY extension point | Evidence required before enabling |
|---|---|---|---|
| SENSO | Retrieve trusted passages from the synthetic retailer knowledge base | `paypilot.integrations.SensoClient`, `RecoveryMission._search_candidates`, and recovery audit | Live API key, seeded documents processed to `complete`, and successful `/org/search/context` call; claim eval endpoints are not published, so deterministic local claim checks remain explicitly local |
| SUPERLINKED | Rerank eligible products through SIE `/v1/score` | `paypilot.integrations.SuperlinkedRanker` and `RecoveryMission._search_candidates` for initial and replacement search | Live API key plus an explicitly selected current Serverless model; returned `item_id`/score mapping and latency are audited; local ranking is labeled fallback |
| FIREDRILL | Adversarial simulation of inventory changes, checkout failure, and policy violations | `paypilot.recovery.RecoveryMission` and `/api/v1/recovery/*` | OAuth/MCP or official runner authorization, then one real sandboxed drill with retained operation/report IDs; no fake recipe or run ID is committed |
| AMPLITUDE | Optional behavioral and recovery-funnel events | `ShopAgent.run` phase transitions and `paypilot.web` request boundary | Opt-in configuration, redaction rules, non-blocking delivery, and event tests |

Implementation status: `paypilot/integrations.py` contains the SIE and Senso clients, `scripts/seed_senso.py` seeds the fictional documents in `examples/senso_kb/`, and `integrations_eval.py` covers sanitized response fixtures. Set `RELAY_REQUIRE_SENSO_EVIDENCE=1` to fail closed when trusted context is unavailable. Sponsor credentials remain only as placeholders in `.env.example`; no live sponsor call was possible in this environment because all relevant credentials were unset.
