# RELAY integration plan

This is a foundation document. No sponsor service is connected by this phase, and the application must not claim otherwise.

| Sponsor | Planned responsibility | Existing RELAY extension point | Evidence required before enabling |
|---|---|---|---|
| SENSO | Retrieve merchant policies, product evidence, and verify claims | `paypilot.catalog.search`, `paypilot.reasoning`, and the `ShopAgent` SEARCH/EXPLAIN phases | Authenticated client, documented response mapping, timeout/fallback tests, and claim-source audit entries |
| SUPERLINKED | Task-specific product ranking through actual SIE Serverless inference | `paypilot.catalog.search` ranking boundary and `ShopAgent` SELECT phase | Real SIE request/response contract, deterministic fallback, ranking tests, and provenance in candidates |
| FIREDRILL | Adversarial simulations for inventory changes, checkout failure, and policy violations | `paypilot.paypal.PayPalClient`, `MockPayPal`, and the future recovery state machine | Reproducible scenarios, no real payment path, exactly-once assertions, and audit output |
| AMPLITUDE | Optional behavioral and recovery-funnel events | `ShopAgent.run` phase transitions and `paypilot.web` request boundary | Opt-in configuration, redaction rules, non-blocking delivery, and event tests |

Implementation order: define provider-neutral interfaces; implement SENSO evidence retrieval; add SUPERLINKED ranking behind an explicit feature flag; implement FIREDRILL scenarios against the mock client; then add opt-in AMPLITUDE events. Credentials are represented only by placeholders in `.env.example` until each integration is implemented.
