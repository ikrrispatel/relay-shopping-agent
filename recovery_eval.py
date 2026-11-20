"""Deterministic Phase 2 acceptance tests for RELAY recovery."""
import json
import sys

from paypilot.catalog import load_catalog, Product
from paypilot.mock_paypal import MockPayPal
from paypilot.paypal import PayPalClient
from paypilot.policy import Policy
from paypilot.recovery import Cart, RecoveryError, RecoveryMission, RecoveryState
from paypilot.integrations import SuperlinkedRanker


PRODUCTS = load_catalog("examples/catalog.csv")


def mission(products=PRODUCTS, policy=None):
    mock = MockPayPal()
    # Keep acceptance tests deterministic and independent of developer-local
    # sponsor credentials; live sponsor smoke tests run separately.
    ranker = SuperlinkedRanker(api_key="", model="")
    return RecoveryMission(products, policy=policy,
                           paypal=PayPalClient(mock=mock), ranker=ranker), mock


def expect(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS  " + name)


def charger(m=None):
    m = m or mission()[0]
    m.start("Find a compatible USB-C laptop charger under $60 with fast delivery")
    expect("initial_candidate_selected", m.chosen.product.id == "T005")
    expect("initial_state_awaiting_approval", m.state == RecoveryState.AWAITING_APPROVAL)
    return m


def main():
    m, mock = mission(); charger(m)
    original = m.cart
    m.approve(original.fingerprint)
    expect("approval_binds_exact_cart", m.state == RecoveryState.APPROVED)
    m.inject_inventory_fault(stock=0)
    m.checkout()
    expect("out_of_stock_rejected", m.state == RecoveryState.REAPPROVAL_REQUIRED)
    expect("no_capture_on_rejected_cart", len(mock.orders) == 0 and not m.capture_id)
    expect("replacement_selected", m.chosen.product.id == "T006")
    try:
        m.approve(original.fingerprint)
        raise AssertionError("old approval accepted")
    except RecoveryError as exc:
        expect("original_approval_rejected_for_replacement", exc.code == "CART_APPROVAL_MISMATCH")
    m.approve(m.cart.fingerprint)
    m.checkout()
    expect("replacement_completed", m.state == RecoveryState.COMPLETED)
    expect("exactly_one_capture", len(mock.orders) == 1 and bool(m.capture_id))
    m.checkout()
    expect("duplicate_checkout_idempotent", len(mock.orders) == 1)

    m, mock = mission(); charger(m); fp = m.cart.fingerprint; m.approve(fp)
    m.inject_inventory_fault(price=65)
    m.inject_inventory_fault(product_id="T006", price=65)
    m.checkout()
    expect("price_increase_beyond_budget_safe_failure", m.state == RecoveryState.FAILED)
    expect("price_failure_no_capture", not mock.orders)

    m, mock = mission(); charger(m); m.approve(m.cart.fingerprint)
    m.inject_inventory_fault(price=40); m.checkout()
    expect("price_decrease_revalidation_requires_reapproval", m.state == RecoveryState.REAPPROVAL_REQUIRED)
    expect("price_decrease_uses_new_cart", m.cart.price == 39.0 and m.cart.fingerprint != "")

    m, mock = mission(); charger(m); m.approve(m.cart.fingerprint)
    m.inject_inventory_fault(merchant="OtherMerchant"); m.checkout()
    expect("merchant_change_invalidates_approval", m.state == RecoveryState.REAPPROVAL_REQUIRED)

    m, mock = mission(); charger(m); original = m.cart; m.approve(m.cart.fingerprint)
    m.inject_inventory_fault(stock=0); m.checkout(); changed = m.cart
    try:
        quantity_two = Cart.make(m.chosen.product, quantity=2,
                                 inventory_version=2, quote_version="quote-2")
        m.approve(quantity_two.fingerprint)
        raise AssertionError("bad fingerprint accepted")
    except RecoveryError as exc:
        expect("quantity_or_snapshot_change_rejected", exc.code == "CART_APPROVAL_MISMATCH")

    m, mock = mission(); charger(m); m.approve(m.cart.fingerprint); m.inject_inventory_fault(stock=0); m.checkout()
    m.approve(m.cart.fingerprint, approved=False)
    expect("user_rejects_replacement_safely", m.state == RecoveryState.FAILED and not mock.orders)

    m, mock = mission(); charger(m)
    try:
        m.checkout(); raise AssertionError("unapproved checkout accepted")
    except RecoveryError as exc:
        expect("invalid_state_transition_blocked", exc.code == "CHECKOUT_NOT_APPROVED")

    m, mock = mission(); charger(m); m.approve(m.cart.fingerprint); m.inject_inventory_fault(stock=0); m.checkout(); m.approve(m.cart.fingerprint); m.checkout()
    audit = m.to_dict()["audit"]
    states = [e["next_state"] for e in audit]
    expect("audit_contains_failed_attempt", "CHECKOUT_REJECTED" in states)
    expect("audit_contains_successful_attempt", "COMPLETED" in states and m.capture_id in json.dumps(audit))
    expect("audit_is_structured_json", json.loads(json.dumps(m.to_dict()))["mission_id"] == m.mission_id)

    # No replacement: isolate the only eligible charger.
    only = [p for p in PRODUCTS if p.id == "T005"]
    m, mock = mission(only); charger(m); m.approve(m.cart.fingerprint); m.inject_inventory_fault(stock=0); m.checkout()
    expect("no_replacement_safe_failure", m.state == RecoveryState.FAILED and not mock.orders)

    print("ALL RECOVERY TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FAIL  %s" % exc)
        sys.exit(1)
