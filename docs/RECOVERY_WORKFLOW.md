# RELAY recovery workflow

This document describes the next executable behavior. The initial RELAY foundation does not yet implement this workflow.

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

Already present: intent parsing, candidate evidence, ranking, policy enforcement, approval gating, mock order/capture, receipt rendering, and per-phase `RunResult.steps` audit evidence in `paypilot/agent.py`. The current mock path completes a single happy-path order and does not model stale inventory or rejected capture.

Needs development: a cart/authorization identity and revalidation boundary; typed stale-state and capture-rejection outcomes in `paypilot.paypal`/`paypilot.mock_paypal`; alternative selection after failure; fresh-approval state; exactly-once idempotency; durable or explicitly returned recovery audit records; and executable tests covering failure, recovery, and no-double-charge behavior.

## Test contract

The recovery tests must run entirely against a deterministic mock. They must demonstrate: stale state causes the original capture to fail; the original authorization is not reused; a replacement is re-ranked and policy-checked; approval is requested again; one and only one replacement capture is completed; and the audit trail names both the failure and recovery. Until those tests pass, RELAY should describe recovery as planned rather than implemented.
