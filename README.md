# RELAY

**Shopping that recovers when things go wrong.**

RELAY is an evidence-backed personal shopping agent that refuses to turn a stale approved cart into a surprise charge. It searches a catalog, applies deterministic spending policy, binds approval to an exact cart snapshot, detects inventory or price changes at checkout, finds a constrained replacement, requests fresh approval, and completes at most one valid test-mode transaction.

The repository is deliberately transparent about its lineage: RELAY reuses the open-source PayPilot shopping foundation and adds the recovery engine, cart authorization model, failure injection, idempotency, audit lifecycle, API routes, UI console, and acceptance tests described below. The internal `paypilot` package name is retained for compatibility with the imported code and its tests.

## Judge quick start

The default path is offline and uses an in-process mock payment client. No API keys or real payment credentials are required.

```bash
git clone https://github.com/ikrrispatel/relay-shopping-agent.git
cd relay-shopping-agent
python3 eval.py
python3 recovery_eval.py
python3 -m paypilot serve --port 8780
```

Then open <http://127.0.0.1:8780/recovery> and use the recovery console, or run the existing scripted demo at `python3 -m paypilot demo`.

The canonical recovery scenario is:

```text
Find a compatible USB-C laptop charger under $60 with fast delivery
```

RELAY selects a charger, waits for exact-cart approval, injects a test-only inventory change, rejects the stale cart without capture, selects the eligible alternative, requires new approval, and completes exactly one mock capture. The state machine and audit events are real backend state, not a scripted transcript.

## What is implemented

### Recovery engine

- Typed mission states from `REQUEST_RECEIVED` through `COMPLETED` or `FAILED`.
- Canonical cart snapshots containing product, quantity, merchant, price, shipping, total, currency, inventory version, and quote version.
- SHA-256 cart fingerprints used to bind approval to one exact cart.
- Revalidation immediately before checkout; stale inventory, price, merchant, or quantity changes cannot reuse approval.
- Deterministic test-only inventory/price/merchant fault injection.
- Replacement search through the existing catalog ranking interface with hard constraints preserved.
- Fresh approval for every replacement cart.
- Stable idempotency keys and duplicate-checkout protection in mock mode.
- Structured JSON audit events containing mission IDs, state transitions, cart fingerprints, policy codes, checkout attempts, and the actual final capture ID.

### Existing shopping foundation

- Offline intent parsing with optional OpenAI-compatible reasoning.
- Evidence-backed product search and ranking.
- Deterministic category, merchant, per-order, daily-spend, and approval guardrails.
- Mock and PayPal Sandbox checkout adapters.
- Receipts, CLI, stdlib web interface, REST API, OpenAPI document, policy audit grid, and Renewal Radar.

## Architecture

```text
request
  └─► reasoning.py ─► catalog.search ─► policy.check
                              │              │
                              ▼              ▼
                         Cart snapshot ─► exact approval
                                                │
                                                ▼
                    revalidate inventory/price/merchant/version
                         ├─ valid ─► idempotent checkout ─► receipt
                         └─ stale ─► reject ─► re-search ─► fresh approval
```

Key modules:

- `paypilot/recovery.py` — mission state machine, cart fingerprints, recovery selection, audit records, and recovery console.
- `paypilot/agent.py` — inherited shopping loop and evidence-cited decision trail.
- `paypilot/catalog.py` — catalog loading, ranking, and evidence generation.
- `paypilot/policy.py` — hard spending decisions that the model cannot override.
- `paypilot/mock_paypal.py` / `paypilot/paypal.py` — deterministic mock and PayPal Sandbox-compatible checkout boundaries.
- `paypilot/api.py` / `paypilot/web.py` — existing API/UI architecture plus recovery lifecycle routes and `/recovery`.
- `recovery_eval.py` — Phase 2 acceptance tests.

Recovery API routes:

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/recovery/start` | Start a mission and prepare its first cart |
| `GET` | `/api/v1/recovery/{mission_id}` | Read state, cart, receipt, and audit |
| `POST` | `/api/v1/recovery/{mission_id}/approve` | Approve or reject an exact fingerprint |
| `POST` | `/api/v1/recovery/{mission_id}/fault` | Inject a mock-only inventory/price/merchant change |
| `POST` | `/api/v1/recovery/{mission_id}/checkout` | Revalidate, recover if needed, or complete |

## Verification

The current verified results are:

```text
python3 eval.py          172 passed, 0 failed
python3 recovery_eval.py all Phase 2 acceptance tests passed
```

The recovery suite covers normal checkout, out-of-stock recovery, price increases and decreases, merchant changes, quantity/fingerprint mismatch, fresh approval, duplicate submission idempotency, safe failure without a replacement, invalid transitions, and audit evidence for the failed and successful attempts.

## Inherited foundation versus RELAY work

| Area | Reused from PayPilot | Implemented for RELAY |
| --- | --- | --- |
| Shopping loop | Intent parsing, catalog ranking, policy checks, approval gate, mock checkout | Recovery invokes these boundaries and preserves their evidence |
| Authorization | Existing boolean approval for a single run | Cart snapshot, canonical fingerprint, single-use approval, reapproval |
| Checkout | Mock/PayPal order and capture adapters | Idempotency key support and stale-cart rejection boundary |
| Reliability | Existing receipts and decision trail | Typed recovery states, rejected-attempt events, replacement workflow, structured audit |
| Product surface | CLI, web UI, REST API, grid, Renewal Radar | Recovery API lifecycle and `/recovery` console |
| Testing | Imported 172-assertion offline suite | `recovery_eval.py` deterministic Phase 2 acceptance suite |

This separation is intentional. RELAY does not claim authorship of the imported PayPilot components, and it does not hide them behind a cosmetic rename.

## Local configuration and security

```bash
cp .env.example .env
```

Mock mode is the default and is the recommended development/test mode. Never commit `.env`, API keys, payment secrets, or personal access tokens. Live mode, if explicitly enabled, targets the PayPal Sandbox only and requires user-supplied credentials. Mission storage is currently in-memory; durable persistence and real merchant inventory adapters are not yet implemented.

## Roadmap

- Durable mission and audit persistence across restarts.
- Real merchant inventory and quote-version adapters.
- SENSO evidence and claim verification.
- SUPERLINKED SIE Serverless ranking behind the existing ranking boundary.
- FIREDRILL adversarial recovery simulations.
- Optional, redacted AMPLITUDE recovery-funnel events.

The sponsor roadmap is documented in [docs/INTEGRATION_PLAN.md](docs/INTEGRATION_PLAN.md), and the implemented recovery contract is documented in [docs/RECOVERY_WORKFLOW.md](docs/RECOVERY_WORKFLOW.md).

## Open-source attribution

The reusable application foundation was imported from [Zhiyilang/paypilot](https://github.com/Zhiyilang/paypilot), commit `18196c6c6ed4ca12d99b06bd9dc76eb42f24fad6`, under the MIT License. The original copyright notice and MIT license remain in [LICENSE](LICENSE). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [docs/UPSTREAM_NOTICE.md](docs/UPSTREAM_NOTICE.md) for the component boundary and provenance. This repository is an independent repository, not a GitHub fork, and no upstream Git metadata was imported.
