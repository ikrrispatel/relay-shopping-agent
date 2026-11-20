"""Offline contract tests for the live sponsor adapters."""
import json

from paypilot.catalog import load_catalog, search
from paypilot.integrations import SensoClient, SuperlinkedRanker
from paypilot.policy import Policy
from paypilot.mock_paypal import MockPayPal
from paypilot.paypal import PayPalClient
from paypilot.recovery import RecoveryMission


PRODUCTS = load_catalog("examples/catalog.csv")


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS  " + name)


def main():
    cands = search(PRODUCTS, keywords=["charger"], category="electronics",
                   max_price=60, required_keywords=["charger", "laptop"])
    captured = {}
    def sie_request(method, url, headers, payload):
        captured.update({"method": method, "url": url, "headers": headers, "payload": payload})
        return 200, {"model": payload["model"], "query_id": "q-test",
                     "scores": [{"item_id": c.product.id, "score": float(i), "rank": i}
                                for i, c in enumerate(reversed(cands))]}, {"X-Request-Id": "req-test"}, 0
    ranker = SuperlinkedRanker(api_key="test-key", model="serverless-test-model",
                               request_fn=sie_request)
    result = ranker.rank("charger", cands)
    check("sie_parses_scores", result["status"] == "success")
    check("sie_uses_model_path", captured["url"].endswith("/score/serverless-test-model"))
    check("sie_uses_bearer_auth", captured["headers"].get("Authorization", "").startswith("Bearer "))
    check("sie_uses_documented_request_shape",
          captured["payload"]["query"] == {"text": "charger"} and
          len(captured["payload"]["items"]) == len(cands))
    check("sie_preserves_item_mapping", set(x["item_id"] for x in result["scores"]) == {c.product.id for c in cands})
    check("sie_returns_provider_metadata", result["correlation_id"] == "q-test" and result["model"] == "serverless-test-model")
    bad = SuperlinkedRanker(api_key="test-key", model="serverless-test-model",
                            request_fn=lambda *a: (200, {"scores": [{"item_id": "not-eligible", "score": 1}]}, {}, 0))
    check("sie_rejects_unknown_candidates", bad.rank("charger", cands)["status"] == "error")
    # Explicit empty injection keeps this test hermetic even when a developer
    # has real sponsor credentials in the repository-local .env.
    check("sie_unconfigured_is_explicit",
          SuperlinkedRanker(api_key="", model="").rank("charger", cands)["status"] == "unconfigured")

    def senso_request(method, url, headers, payload=None):
        if method == "POST":
            return 200, {"results": [{"content_id": "content-1", "kb_node_id": "node-1",
                                      "version_id": "version-1", "chunk_text": "T005 charger supports laptop USB-C",
                                      "score": .91, "rank": 1}]}, {}, 0
        return 200, {"org_id": "org-1"}, {}, 0
    senso = SensoClient(api_key="test-key", request_fn=senso_request)
    context = senso.search_context("charger")
    check("senso_parses_context_sources", context["status"] == "success" and context["results"][0]["content_id"] == "content-1")
    check("senso_supported_claim", senso.local_claim_check("T005 charger supports laptop USB-C", context["results"])["verdict"] == "supported")
    check("senso_unsupported_claim", senso.local_claim_check("merchant guarantees tomorrow", context["results"])["verdict"] == "unsupported")
    check("senso_conflicting_claim", senso.local_claim_check("T005 is a phone case", context["results"])["verdict"] == "conflicting")

    class FakeRanker:
        def rank(self, query, candidates):
            return {"provider": "superlinked", "status": "success", "model": "fixture",
                    "scores": [{"item_id": c.product.id, "score": 100 - i, "rank": i}
                               for i, c in enumerate(reversed(candidates))],
                    "selected_product_id": candidates[-1].product.id}
    class FakeSenso:
        def search_context(self, query, max_results=5):
            return {"provider": "senso", "status": "success", "query": query,
                    "results": [{"content_id": "fixture-content", "chunk_text": "synthetic evidence"}]}
    m = RecoveryMission(PRODUCTS, policy=Policy(), brain=None,
                        paypal=PayPalClient(mock=MockPayPal()), ranker=FakeRanker(),
                        context_provider=FakeSenso())
    m.start("Find a compatible USB-C laptop charger under $60 with fast delivery")
    check("sponsor_status_attached_to_mission", m.integrations["superlinked"]["status"] == "success" and m.integrations["senso"]["status"] == "success")
    check("sponsor_evidence_attached_to_audit", "fixture-content" in json.dumps(m.to_dict()["audit"]))
    check("no_api_key_in_serialized_audit", "test-key" not in json.dumps(m.to_dict()))
    print("ALL INTEGRATION TESTS PASSED")


if __name__ == "__main__":
    main()
