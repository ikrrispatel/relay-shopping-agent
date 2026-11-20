# Third-party notices

## PayPilot application foundation

- Source: https://github.com/Zhiyilang/paypilot
- Original project: PayPilot
- Imported upstream commit: `18196c6c6ed4ca12d99b06bd9dc76eb42f24fad6`
- License: MIT, copyright (c) 2026 Zhiyilang; see [LICENSE](LICENSE).

Imported components include the Python package, catalog and example data, offline/LLM reasoning interfaces, agent orchestration, deterministic policy engine, mock and PayPal clients, receipts, REST API, web interface, CLI, audit grid, Renewal Radar, OpenAPI specification, evaluation suite, and runtime configuration.

RELAY changes include the recovery state machine, cart-bound authorization, revalidation and replacement flow, idempotent mock checkout, structured recovery audit, recovery API/UI, RELAY configuration placeholders, repository hygiene, and project documentation. The internal `paypilot` package name is retained to avoid breaking inherited imports and tests. No upstream Git metadata was imported, and this repository is not a fork. Redundant upstream README copies were consolidated into [docs/UPSTREAM_NOTICE.md](docs/UPSTREAM_NOTICE.md); the required MIT license and copyright notice remain intact.
