# RELAY recovery workflow

This document describes the executable Phase 2 behavior implemented in `paypilot/recovery.py`.

## Target sequence

1. Parse a constrained request with `paypilot.reasoning.OfflineBrain.parse` or the existing LLM brain.
2. Retrieve eligible candidates with `paypilot.catalog.search`, preserving candidate evidence.
3. Select a candidate and evaluate `paypilot.policy.check` before payment.
4. Prepare a specific cart/order through `paypilot.paypal.PayPalClient.create_order`.
5. Require explicit approval for held orders, matching the current `ShopAgent.run(..., approve=True)` behavior.
6. Revalidate product facts, inventory, merchant policy, price, and the cart authorization immediately before capture.
7. If capture rejects a stale state, mark the original authorization unusable and record the failure as an audit step; never retry the same authorization blindly.
8. Search again for a valid alternative, re-run policy checks, and request fresh approval for the replacement.
9. Complete exactly one valid test-mode order and extract its capture with `paypilot.agent._extract_capture`.
10. Render a receipt and persist an audit trail containing the original selection, approval, stale-state failure, recovery selection, fresh approval, and final transaction.

## Existing versus new

Already present and reused: intent parsing, candidate evidence, ranking, policy enforcement, approval gating, mock order/capture, receipt rendering, and per-phase `RunResult.steps` audit evidence in `paypilot/agent.py`.

Phase 2 adds: `Cart` fingerprints and inventory versions; typed state transitions in `RecoveryMission`; test-only inventory/price/merchant faults; alternative selection after failure; fresh approval; mock idempotency keys; structured audit events; and API/UI lifecycle controls. The mission registry is in-memory, so persistence across process restarts remains future work.

## Test contract

Run `python3 recovery_eval.py` for the deterministic Phase 2 acceptance suite. It demonstrates stale state causes rejection; the original authorization is not reused; replacements are re-ranked and policy-checked; fresh approval is requested; one and only one replacement capture completes; duplicate checkout is idempotent; and the structured audit names both failure and recovery.

## API lifecycle

- `POST /api/v1/recovery/start` with `{request}` creates a mission.
- `GET /api/v1/recovery/{mission_id}` returns current state, cart, receipt, and audit.
- `POST /api/v1/recovery/{mission_id}/approve` requires the exact cart `fingerprint`.
- `POST /api/v1/recovery/{mission_id}/fault` injects a mock-only inventory/price/merchant change.
- `POST /api/v1/recovery/{mission_id}/checkout` revalidates and either completes or enters recovery.

The `/recovery` page uses these same routes and renders the returned backend state. Fault injection is disabled when the server runs in live/sandbox mode.
