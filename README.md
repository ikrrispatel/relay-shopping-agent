# RELAY

**Shopping that recovers when things go wrong.**

RELAY is an evidence-backed personal shopping agent that safely handles failed purchases. The current baseline imports the working PayPilot shopping application and keeps its internal `paypilot` package name for compatibility. RELAY branding and recovery specifications are layered on top without rewriting the tested foundation.

## What works now

- Natural-language shopping intent parsing with an offline deterministic brain.
- Evidence-backed catalog search and product ranking.
- Spending policy enforcement: category allowlist, merchant blocklist, per-order cap, daily cap, and approval threshold.
- Human approval flow before higher-value purchases.
- Mock PayPal OAuth/order/capture flow, receipts, and decision-trail audit evidence.
- CLI demo and shop commands, stdlib web UI, policy audit grid, REST API, OpenAPI document, and Renewal Radar.

The default mode is offline mock mode. No real payment is made.

## Local setup

```bash
git clone https://github.com/ikrrispatel/relay-shopping-agent.git
cd relay-shopping-agent
cp .env.example .env
python3 eval.py
python3 -m paypilot demo
python3 -m paypilot serve --port 8780
```

Open <http://127.0.0.1:8780>. The API is available under `/api/v1`; useful routes include `/api/v1/health`, `/api/v1/catalog`, `/api/v1/quote`, `/api/v1/policy/check`, and `/api/v1/agent/run`. `/openapi.yaml`, `/docs`, `/grid`, and `/renewals` are also served by the application.

## Tests

`python3 eval.py` is the inherited offline evaluation suite. It covers catalog loading, intent parsing, ranking, every policy guardrail, approval, mock checkout, receipts, audit-grid rendering, and HTTP routes. The exact result from the imported upstream baseline is recorded in the engineering handoff; the HTTP portion requires permission to bind a local socket in restricted environments.

## Architecture

The reusable implementation remains in `paypilot/`:

- `agent.py` — perceive, search, select, policy, approval, explain, execute, capture.
- `catalog.py` — product data and evidence-cited ranking.
- `policy.py` — deterministic spending guardrails.
- `reasoning.py` — offline and optional OpenAI-compatible brains.
- `paypal.py` / `mock_paypal.py` — sandbox client and offline mock.
- `receipts.py` — receipts and decision trails.
- `web.py` / `api.py` / `grid.py` — UI, REST routes, and audit console.

## Planned, not yet implemented

The failed-checkout recovery workflow and sponsor integrations are specified in `docs/RECOVERY_WORKFLOW.md` and `docs/INTEGRATION_PLAN.md`. They are not represented as connected or complete until executable tests demonstrate them. The next implementation must add stale-cart detection, invalidated authorization handling, alternative search, fresh approval, exactly-once test-mode completion, and recovery audit events.

## Attribution and licensing

The application foundation was imported from [Zhiyilang/paypilot](https://github.com/Zhiyilang/paypilot), commit `18196c6c6ed4ca12d99b06bd9dc76eb42f24fad6`. It is MIT-licensed; the original `LICENSE` is retained. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the preserved [upstream README](docs/UPSTREAM_README.md) for details. This repository is not a GitHub fork.

## Security

Never commit `.env`, API keys, payment secrets, or personal access tokens. Use mock mode for development and tests. Live mode, when used, must target a PayPal sandbox with user-supplied credentials; RELAY does not authorize real payments in this initial release.
