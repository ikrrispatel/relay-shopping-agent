"""RELAY purchase-recovery state machine.

This module owns mission state, cart-bound approvals, deterministic inventory
faults, and recovery audit events.  It deliberately depends on the existing
catalog, policy, brain, and payment interfaces instead of replacing them.
"""
import copy
import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field

from . import catalog as catalog_mod
from .agent import _extract_capture
from .policy import Policy, check as policy_check
from .reasoning import OfflineBrain
from .integrations import (AmplitudeAnalytics, FiredrillRunner, SensoClient,
                           SuperlinkedRanker)


class RecoveryError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class RecoveryState:
    REQUEST_RECEIVED = "REQUEST_RECEIVED"
    CANDIDATES_FOUND = "CANDIDATES_FOUND"
    CART_PREPARED = "CART_PREPARED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REVALIDATING = "REVALIDATING"
    CHECKOUT_REJECTED = "CHECKOUT_REJECTED"
    RECOVERY_SEARCH = "RECOVERY_SEARCH"
    REPLACEMENT_PREPARED = "REPLACEMENT_PREPARED"
    REAPPROVAL_REQUIRED = "REAPPROVAL_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


_TRANSITIONS = {
    RecoveryState.REQUEST_RECEIVED: {RecoveryState.CANDIDATES_FOUND,
                                     RecoveryState.FAILED},
    RecoveryState.CANDIDATES_FOUND: {RecoveryState.CART_PREPARED,
                                     RecoveryState.FAILED},
    RecoveryState.CART_PREPARED: {RecoveryState.AWAITING_APPROVAL,
                                  RecoveryState.APPROVED,
                                  RecoveryState.FAILED},
    RecoveryState.AWAITING_APPROVAL: {RecoveryState.APPROVED,
                                      RecoveryState.FAILED},
    RecoveryState.APPROVED: {RecoveryState.REVALIDATING},
    RecoveryState.REVALIDATING: {RecoveryState.CHECKOUT_REJECTED,
                                 RecoveryState.COMPLETED,
                                 RecoveryState.FAILED},
    RecoveryState.CHECKOUT_REJECTED: {RecoveryState.RECOVERY_SEARCH,
                                      RecoveryState.FAILED},
    RecoveryState.RECOVERY_SEARCH: {RecoveryState.REPLACEMENT_PREPARED,
                                    RecoveryState.FAILED},
    RecoveryState.REPLACEMENT_PREPARED: {RecoveryState.REAPPROVAL_REQUIRED,
                                         RecoveryState.FAILED},
    RecoveryState.REAPPROVAL_REQUIRED: {RecoveryState.APPROVED,
                                        RecoveryState.FAILED},
    RecoveryState.COMPLETED: set(),
    RecoveryState.FAILED: set(),
}


@dataclass(frozen=True)
class Cart:
    product_id: str
    quantity: int
    merchant: str
    price: float
    shipping: float = 0.0
    total: float = 0.0
    currency: str = "USD"
    inventory_version: int = 0
    quote_version: str = ""
    fingerprint: str = ""

    @classmethod
    def make(cls, product, quantity=1, shipping=0.0, inventory_version=0,
             quote_version=""):
        total = round(product.price * quantity + shipping, 2)
        fields = {
            "product_id": product.id, "quantity": quantity,
            "merchant": product.merchant, "price": round(product.price, 2),
            "shipping": round(shipping, 2), "total": total,
            "currency": "USD", "inventory_version": inventory_version,
            "quote_version": quote_version,
        }
        raw = json.dumps(fields, sort_keys=True, separators=(",", ":"))
        return cls(**fields, fingerprint=hashlib.sha256(raw.encode()).hexdigest())


@dataclass
class AuditEvent:
    timestamp: float
    mission_id: str
    previous_state: str
    next_state: str
    reason: str
    cart_id: str = ""
    fingerprint: str = ""
    policy_code: str = ""
    checkout_attempt: str = ""
    details: dict = field(default_factory=dict)


class RecoveryMission:
    """One purchase mission. User approvals are exact-cart and single-use."""

    def __init__(self, products, policy=None, brain=None, paypal=None,
                 daily_spent=0.0, mission_id=None, test_mode=True,
                 ranker=None, context_provider=None, analytics=None):
        self.products = products
        self.policy = policy or Policy()
        self.brain = brain or OfflineBrain()
        self.paypal = paypal
        self.daily_spent = daily_spent
        self.mission_id = mission_id or "mission-" + uuid.uuid4().hex[:12]
        self.test_mode = bool(test_mode)
        self.ranker = ranker or SuperlinkedRanker()
        self.senso = context_provider or SensoClient()
        self.analytics = analytics or AmplitudeAnalytics(api_key="")
        self.integrations = {"firedrill": FiredrillRunner().status(),
                             "amplitude": {"provider": "amplitude",
                                           "status": self.analytics.status}}
        self.require_senso = os.environ.get("RELAY_REQUIRE_SENSO_EVIDENCE", "0") == "1"
        self.state = None
        self.request = ""
        self.intent = None
        self.candidates = []
        self.chosen = None
        self.policy_decision = None
        self.cart = None
        self.approval = None
        self.order = None
        self.capture_id = ""
        self.failure_code = ""
        self.failure_reason = ""
        self.audit = []
        self.inventory = {
            p.id: {"stock": p.stock, "price": p.price,
                   "merchant": p.merchant, "version": 1}
            for p in products
        }
        self._checkout_attempts = {}
        self.transition(RecoveryState.REQUEST_RECEIVED, "mission created")

    def transition(self, next_state, reason, cart=None, policy_code="",
                   checkout_attempt="", details=None):
        if self.state is not None and next_state not in _TRANSITIONS[self.state]:
            raise RecoveryError("INVALID_STATE_TRANSITION",
                                "%s -> %s is not allowed" %
                                (self.state, next_state))
        previous = self.state or ""
        self.state = next_state
        self.audit.append(AuditEvent(
            timestamp=time.time(), mission_id=self.mission_id,
            previous_state=previous, next_state=next_state, reason=reason,
            cart_id=cart.product_id if cart else "",
            fingerprint=cart.fingerprint if cart else "",
            policy_code=policy_code, checkout_attempt=checkout_attempt,
            details=details or {}))
        self.analytics.emit_transition(next_state, self.mission_id, reason)

    def _runtime_product(self, product):
        p = copy.copy(product)
        row = self.inventory[p.id]
        p.stock, p.price, p.merchant = row["stock"], row["price"], row["merchant"]
        return p

    def _runtime_products(self):
        return [self._runtime_product(p) for p in self.products]

    def _required_keywords(self):
        # Product nouns are hard constraints; descriptive/operational words
        # remain ranking signals. This keeps recovery from substituting a hub
        # for a charger merely because both mention USB.
        nouns = {"charger", "laptop", "keyboard", "earbuds", "headphones",
                 "scarf", "socks", "mug", "watch", "book", "journal", "hub"}
        return [k for k in self.intent.keywords if k in nouns]

    def _search_candidates(self):
        sort = "price_asc" if self.intent.cheapest else "value"
        candidates = catalog_mod.search(
            self._runtime_products(), keywords=self.intent.keywords,
            category=self.intent.category_hint or None,
            max_price=self.intent.budget, sort=sort,
            required_keywords=self._required_keywords())
        context = self.senso.search_context(self.request, max_results=5)
        self.integrations["senso"] = context
        if self.require_senso and context.get("status") != "success":
            return [], {"reason": "trusted Senso context required", "context": context}
        rank = self.ranker.rank(self.request, candidates)
        self.integrations["superlinked"] = rank
        if rank.get("status") == "success":
            by_id = {c.product.id: c for c in candidates}
            candidates = [by_id[x["item_id"]] for x in rank["scores"]]
        else:
            rank["fallback"] = "local_catalog_score"
        return candidates, {"context": context, "ranking": rank}

    def start(self, request_text):
        if self.state != RecoveryState.REQUEST_RECEIVED:
            raise RecoveryError("MISSION_ALREADY_STARTED", "mission has already started")
        self.request = request_text.strip()
        if not self.request:
            self.transition(RecoveryState.FAILED, "empty request")
            raise RecoveryError("EMPTY_REQUEST", "request is required")
        self.intent = self.brain.parse(self.request)
        self.candidates, search_details = self._search_candidates()
        if not self.candidates:
            self.transition(RecoveryState.FAILED, search_details.get("reason", "no eligible candidates"),
                            details=search_details)
            return self
        self.transition(RecoveryState.CANDIDATES_FOUND,
                        "ranked %d eligible candidate(s)" % len(self.candidates),
                        details=search_details)
        return self._prepare(self.candidates[0], initial=True)

    def _prepare(self, candidate, initial=False):
        self.chosen = candidate
        self.policy_decision = policy_check(self.policy, candidate.product,
                                            self.daily_spent)
        if self.policy_decision.action == "block":
            self.transition(RecoveryState.FAILED, "policy blocked candidate",
                            policy_code=self.policy_decision.rule)
            return self
        row = self.inventory[candidate.product.id]
        self.cart = Cart.make(candidate.product, inventory_version=row["version"],
                              quote_version="quote-%d" % row["version"])
        prepared_state = (RecoveryState.CART_PREPARED if initial
                          else RecoveryState.REPLACEMENT_PREPARED)
        self.transition(prepared_state,
                        "prepared %s cart" % ("initial" if initial else "replacement"),
                        cart=self.cart, policy_code=self.policy_decision.rule)
        self.transition(RecoveryState.AWAITING_APPROVAL if initial
                        else RecoveryState.REAPPROVAL_REQUIRED,
                        "explicit user approval required", cart=self.cart,
                        policy_code=self.policy_decision.rule)
        return self

    def approve(self, fingerprint, approved=True):
        expected = {RecoveryState.AWAITING_APPROVAL, RecoveryState.REAPPROVAL_REQUIRED}
        if self.state not in expected:
            raise RecoveryError("APPROVAL_NOT_ALLOWED", "mission is not awaiting approval")
        if fingerprint != self.cart.fingerprint:
            raise RecoveryError("CART_APPROVAL_MISMATCH",
                                "approval does not match the current cart")
        if not approved:
            self.approval = {"approved": False, "fingerprint": fingerprint,
                             "timestamp": time.time()}
            self.transition(RecoveryState.FAILED, "user rejected cart",
                            cart=self.cart)
            return self
        self.approval = {"approved": True, "fingerprint": fingerprint,
                         "timestamp": time.time(), "used": False}
        self.transition(RecoveryState.APPROVED, "user approved exact cart",
                        cart=self.cart)
        return self

    def inject_inventory_fault(self, product_id=None, stock=None, price=None,
                               merchant=None):
        if not self.test_mode:
            raise RecoveryError("FAULT_INJECTION_DISABLED", "fault injection is test-only")
        pid = product_id or (self.cart.product_id if self.cart else "")
        if pid not in self.inventory:
            raise RecoveryError("UNKNOWN_PRODUCT", "unknown product %s" % pid)
        row = self.inventory[pid]
        if stock is not None:
            row["stock"] = int(stock)
        if price is not None:
            row["price"] = float(price)
        if merchant is not None:
            row["merchant"] = merchant
        row["version"] += 1
        return self

    def _find_replacement(self):
        cands, search_details = self._search_candidates()
        return [c for c in cands if c.product.id != self.cart.product_id]

    def checkout(self):
        if self.state == RecoveryState.COMPLETED:
            return self
        if self.state != RecoveryState.APPROVED:
            raise RecoveryError("CHECKOUT_NOT_APPROVED", "exact cart approval is required")
        if not self.approval or not self.approval.get("approved") or self.approval.get("used"):
            raise RecoveryError("APPROVAL_UNUSABLE", "approval is missing or already used")
        self.transition(RecoveryState.REVALIDATING, "revalidating approved cart",
                        cart=self.cart)
        current = self._runtime_product(self.chosen.product)
        current_row = self.inventory[current.id]
        current_cart = Cart.make(current, inventory_version=current_row["version"],
                                 quote_version="quote-%d" % current_row["version"])
        if current.stock <= 0 or current_cart.fingerprint != self.cart.fingerprint:
            self.approval["used"] = True
            self.failure_code = "STALE_CART" if current.stock > 0 else "OUT_OF_STOCK"
            self.failure_reason = ("cart changed before checkout" if current.stock > 0
                                   else "approved product is no longer available")
            attempt = "attempt-" + uuid.uuid4().hex[:10]
            self.transition(RecoveryState.CHECKOUT_REJECTED, self.failure_reason,
                            cart=self.cart, policy_code=self.failure_code,
                            checkout_attempt=attempt,
                            details={"current_fingerprint": current_cart.fingerprint})
            return self._recover()
        self.approval["used"] = True
        key = "relay-%s-%s" % (self.mission_id, self.cart.fingerprint)
        if key in self._checkout_attempts:
            self.order, self.capture_id = self._checkout_attempts[key]
            self.transition(RecoveryState.COMPLETED, "idempotent checkout replay",
                            cart=self.cart, checkout_attempt=key)
            return self
        if self.paypal is None:
            raise RecoveryError("PAYMENT_UNAVAILABLE", "payment client is not configured")
        attempt = key
        try:
            self.order = self.paypal.create_order(
                self.cart.total, description="RELAY: %s" % self.chosen.product.name,
                ref_id=self.chosen.product.id, idempotency_key=key)
            cap = self.paypal.capture_order(self.order["id"])
            self.capture_id, status = _extract_capture(cap)
            if status != "COMPLETED" or not self.capture_id:
                raise RecoveryError("CAPTURE_NOT_COMPLETED", "checkout did not complete")
        except RecoveryError:
            raise
        except Exception as exc:
            self.failure_code = "CHECKOUT_FAILED"
            self.failure_reason = str(exc)
            self.transition(RecoveryState.FAILED, "payment boundary rejected checkout",
                            cart=self.cart, policy_code=self.failure_code,
                            checkout_attempt=attempt)
            raise RecoveryError(self.failure_code, self.failure_reason)
        self._checkout_attempts[key] = (self.order, self.capture_id)
        self.transition(RecoveryState.COMPLETED, "replacement or original checkout completed",
                        cart=self.cart, checkout_attempt=attempt,
                        details={"capture_id": self.capture_id})
        return self

    def _recover(self):
        self.transition(RecoveryState.RECOVERY_SEARCH, "searching for a valid replacement",
                        cart=self.cart, policy_code=self.failure_code)
        replacements = self._find_replacement()
        for candidate in replacements:
            decision = policy_check(self.policy, candidate.product, self.daily_spent)
            if decision.action != "block":
                self.candidates = replacements
                return self._prepare(candidate, initial=False)
        self.transition(RecoveryState.FAILED, "no valid replacement remains",
                        cart=self.cart, policy_code="NO_REPLACEMENT")
        return self

    def to_dict(self):
        self.integrations["amplitude"] = {
            "provider": "amplitude",
            "status": self.analytics.status,
            "failed_events": len(self.analytics.failures),
        }
        def product_dict(p):
            if not p:
                return None
            return {"id": p.id, "name": p.name, "category": p.category,
                    "price": p.price, "rating": p.rating,
                    "merchant": p.merchant, "stock": p.stock,
                    "tags": list(p.tags)}
        return {
            "mission_id": self.mission_id, "state": self.state,
            "request": self.request,
            "intent": asdict(self.intent) if self.intent else None,
            "candidates": [{"product": product_dict(c.product),
                            "score": c.score, "evidence": list(c.evidence)}
                           for c in self.candidates],
            "chosen": product_dict(self.chosen.product) if self.chosen else None,
            "cart": asdict(self.cart) if self.cart else None,
            "approval": self.approval, "order": self.order,
            "capture_id": self.capture_id, "failure_code": self.failure_code,
            "failure_reason": self.failure_reason,
            "integrations": self.integrations,
            "audit": [asdict(e) for e in self.audit],
            "receipt": {"capture_id": self.capture_id,
                        "status": "completed" if self.state == RecoveryState.COMPLETED
                        else self.state},
        }


class RecoveryService:
    """In-memory mission registry for the existing stdlib API/web server."""

    def __init__(self, products, paypal_factory, policy=None, test_mode=True):
        self.products = products
        self.paypal_factory = paypal_factory
        self.policy = policy or Policy()
        self.test_mode = test_mode
        self.missions = {}

    def start(self, payload):
        mission = RecoveryMission(self.products, policy=self.policy,
                                  brain=OfflineBrain(),
                                  paypal=self.paypal_factory(),
                                  daily_spent=float(payload.get("daily_spent") or 0),
                                  test_mode=self.test_mode,
                                  analytics=AmplitudeAnalytics())
        mission.start((payload.get("request") or "").strip())
        self.missions[mission.mission_id] = mission
        return mission

    def get(self, mission_id):
        mission = self.missions.get(mission_id)
        if not mission:
            raise RecoveryError("UNKNOWN_MISSION", "unknown mission %s" % mission_id)
        return mission

    def handle(self, mission_id, action, payload=None):
        m = self.get(mission_id)
        payload = payload or {}
        if action == "approve":
            m.approve(payload.get("fingerprint", ""), bool(payload.get("approved", True)))
        elif action == "fault":
            m.inject_inventory_fault(payload.get("product_id"),
                                     payload.get("stock", 0), payload.get("price"),
                                     payload.get("merchant"))
        elif action == "checkout":
            m.checkout()
        else:
            raise RecoveryError("NOT_FOUND", "unknown recovery action")
        return m


def render_recovery_page():
    return """<!doctype html><html><head><meta charset='utf-8'><title>RELAY · Recovery</title>
<style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f7f9;color:#1a1f2b;margin:0}.wrap{max-width:900px;margin:auto;padding:28px 20px}.card{background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:20px;margin-top:16px}input,button{padding:9px;border-radius:7px;border:1px solid #d8dde5;margin:4px}button{background:#4f7cff;color:#fff;border:0;cursor:pointer}pre{white-space:pre-wrap;background:#f7f8fa;padding:12px;border-radius:8px}.mut{color:#5a6472}</style></head>
<body><div class='wrap'><h1>RELAY recovery console</h1><p class='mut'>Real mission state from the recovery API. Fault injection is available only in mock/test mode.</p>
<div class='card'><input id='request' size='60' value='Find a compatible USB-C laptop charger under $60 with fast delivery'><button onclick='start()'>Start mission</button><div id='actions'></div></div><div class='card'><pre id='out'>No mission started.</pre></div></div>
<script>let id='';let state={};const out=document.getElementById('out');function show(x){state=x;out.textContent=JSON.stringify(x,null,2);let a=document.getElementById('actions');if(!x.mission_id)return;a.innerHTML=(x.state==='AWAITING_APPROVAL'||x.state==='REAPPROVAL_REQUIRED'?`<button onclick="approve()">Approve exact cart</button><button onclick="reject()">Reject</button>`:'')+(x.state==='APPROVED'?`<button onclick="checkout()">Checkout</button>`:'')+(x.state==='AWAITING_APPROVAL'||x.state==='APPROVED'?`<button onclick="fault()">Inject out-of-stock fault</button>`:'');}async function call(url,body){let r=await fetch(url,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});let j=await r.json();if(!r.ok)throw j;show(j.data||j);};async function start(){try{let r=await fetch('/api/v1/recovery/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request:document.getElementById('request').value})});let j=await r.json();id=j.data.mission_id;show(j.data);}catch(e){out.textContent=JSON.stringify(e,null,2)}}async function approve(){await call('/api/v1/recovery/'+id+'/approve',{fingerprint:state.cart.fingerprint,approved:true})}async function reject(){await call('/api/v1/recovery/'+id+'/approve',{fingerprint:state.cart.fingerprint,approved:false})}async function fault(){await call('/api/v1/recovery/'+id+'/fault',{product_id:state.cart.product_id,stock:0})}async function checkout(){await call('/api/v1/recovery/'+id+'/checkout',{})}</script></body></html>"""
