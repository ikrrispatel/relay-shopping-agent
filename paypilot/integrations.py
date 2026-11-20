"""Optional live sponsor integrations with explicit, auditable fallbacks.

All clients use the Python standard library. Missing credentials never make a
local mock run pretend that a sponsor was contacted.
"""
import json
import os
import time
import urllib.error
import urllib.request
import uuid


class ProviderError(Exception):
    def __init__(self, provider, code, message):
        super().__init__(message)
        self.provider, self.code = provider, code


def _request(method, url, headers=None, payload=None, timeout=20):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw or "{}"), response.headers, started
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:500]
        raise ProviderError("http", "HTTP_%s" % exc.code, raw or str(exc))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ProviderError("http", "REQUEST_FAILED", str(exc))


class SuperlinkedRanker:
    """SIE Serverless score adapter.

    The model is deliberately explicit: it must be selected from Superlinked's
    current Serverless catalog and supplied as SUPERLINKED_MODEL. We do not
    silently claim a default model is available.
    """

    provider = "superlinked"

    def __init__(self, api_key=None, base_url=None, model=None, allow_fallback=True,
                 request_fn=None):
        self.api_key = api_key or os.environ.get("SUPERLINKED_API_KEY") or os.environ.get("SIE_API_KEY", "")
        self.base_url = (base_url or os.environ.get("SUPERLINKED_SIE_URL") or
                         "https://api.superlinked.com/v1").rstrip("/")
        self.model = model or os.environ.get("SUPERLINKED_MODEL", "")
        self.allow_fallback = allow_fallback
        self.request_fn = request_fn or _request

    @property
    def configured(self):
        return bool(self.api_key and self.model)

    def rank(self, query_text, candidates):
        correlation_id = "relay-rank-" + uuid.uuid4().hex[:12]
        if not candidates:
            return {"provider": self.provider, "status": "not_run",
                    "model": self.model, "scores": [], "candidate_ids": [],
                    "selected_product_id": None, "correlation_id": correlation_id}
        if not self.configured:
            return {"provider": self.provider, "status": "unconfigured",
                    "model": self.model, "scores": [],
                    "candidate_ids": [c.product.id for c in candidates],
                    "selected_product_id": None, "correlation_id": correlation_id,
                    "error": "SUPERLINKED_API_KEY/SIE_API_KEY and SUPERLINKED_MODEL are required"}
        items = [{"id": c.product.id, "text": self._representation(c)}
                 for c in candidates]
        payload = {"model": self.model, "query": {"text": query_text},
                   "items": items, "query_id": correlation_id}
        started = time.perf_counter()
        try:
            _, body, headers, _ = self.request_fn(
                "POST", self.base_url + "/score",
                {"Authorization": "Bearer " + self.api_key}, payload)
            scores = self._parse_scores(body, {c.product.id for c in candidates})
            return {"provider": self.provider, "status": "success", "model": body.get("model", self.model),
                    "scores": scores, "candidate_ids": [c.product.id for c in candidates],
                    "selected_product_id": scores[0]["item_id"] if scores else None,
                    "correlation_id": body.get("query_id") or headers.get("X-Request-Id") or correlation_id,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
        except (ProviderError, ValueError) as exc:
            return {"provider": self.provider, "status": "error", "model": self.model,
                    "scores": [], "candidate_ids": [c.product.id for c in candidates],
                    "selected_product_id": None, "correlation_id": correlation_id,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "error": str(exc)}

    @staticmethod
    def _representation(candidate):
        p = candidate.product
        return ("product_id=%s; name=%s; category=%s; merchant=%s; price=%.2f; "
                "rating=%.1f; stock=%d; tags=%s; evidence=%s" %
                (p.id, p.name, p.category, p.merchant, p.price, p.rating,
                 p.stock, ",".join(p.tags), "; ".join(candidate.evidence)))

    @staticmethod
    def _parse_scores(body, candidate_ids):
        entries = body.get("scores") if isinstance(body, dict) else None
        if not isinstance(entries, list):
            raise ValueError("SIE response has no scores list")
        parsed = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("item_id") not in candidate_ids:
                raise ValueError("SIE returned an unknown or malformed item_id")
            try:
                parsed.append({"item_id": entry["item_id"], "score": float(entry["score"]),
                               "rank": int(entry.get("rank", len(parsed)))})
            except (KeyError, TypeError, ValueError):
                raise ValueError("SIE score entry is malformed")
        if {x["item_id"] for x in parsed} != candidate_ids:
            raise ValueError("SIE did not return exactly the eligible candidates")
        return sorted(parsed, key=lambda x: x["score"], reverse=True)


class SensoClient:
    """Senso context retrieval and idempotent synthetic-KB seeding."""

    provider = "senso"

    def __init__(self, api_key=None, base_url=None, request_fn=None):
        self.api_key = api_key or os.environ.get("SENSO_API_KEY", "")
        self.base_url = (base_url or os.environ.get("SENSO_BASE_URL") or
                         "https://apiv2.senso.ai/api/v1").rstrip("/")
        self.request_fn = request_fn or _request

    @property
    def configured(self):
        return bool(self.api_key)

    def _headers(self):
        return {"X-API-Key": self.api_key, "X-Senso-Signals": "off"}

    def health(self):
        if not self.configured:
            return {"provider": self.provider, "status": "unconfigured"}
        try:
            code, body, _, _ = self.request_fn("GET", self.base_url + "/org/me", self._headers())
            return {"provider": self.provider, "status": "success" if code == 200 else "error",
                    "org_id": body.get("org_id"), "name": body.get("name")}
        except ProviderError as exc:
            return {"provider": self.provider, "status": "error", "error": str(exc)}

    def search_context(self, query, content_ids=None, max_results=5):
        if not self.configured:
            return {"provider": self.provider, "status": "unconfigured", "results": [],
                    "query": query}
        payload = {"query": query, "max_results": min(int(max_results), 20)}
        if content_ids:
            payload["content_ids"] = list(content_ids)
            payload["require_scoped_ids"] = True
        try:
            _, body, _, started = self.request_fn("POST", self.base_url + "/org/search/context",
                                                  self._headers(), payload)
            results = []
            for index, item in enumerate(body.get("results", []), 1):
                results.append({"content_id": item.get("content_id"),
                                "kb_node_id": item.get("kb_node_id"),
                                "version_id": item.get("version_id"),
                                "chunk_text": item.get("chunk_text", ""),
                                "score": item.get("score"),
                                "rank": item.get("rank", index)})
            return {"provider": self.provider, "status": "success", "query": query,
                    "results": results,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
        except ProviderError as exc:
            return {"provider": self.provider, "status": "error", "query": query,
                    "results": [], "error": str(exc)}

    def seed_documents(self, documents, poll_seconds=5, max_wait=120):
        """Upload synthetic raw documents and wait for complete processing.

        Senso itself returns 409 for identical text; that response is treated as
        already seeded and never duplicated. The caller can persist returned IDs.
        """
        if not self.configured:
            raise ProviderError(self.provider, "UNCONFIGURED", "SENSO_API_KEY is not set")
        seeded = []
        for document in documents:
            try:
                _, body, _, _ = self.request_fn("POST", self.base_url + "/org/kb/raw",
                                              self._headers(), document)
                node_id = body.get("kb_node_id") or body.get("id")
                seeded.append({"title": document.get("title"), "status": "submitted",
                               "kb_node_id": node_id})
            except ProviderError as exc:
                if exc.code != "HTTP_409":
                    raise
                seeded.append({"title": document.get("title"), "status": "already_exists"})
        deadline = time.time() + max_wait
        for item in seeded:
            node_id = item.get("kb_node_id")
            if not node_id or item["status"] == "already_exists":
                continue
            while time.time() < deadline:
                _, body, _, _ = self.request_fn("GET", self.base_url + "/org/kb/nodes/" + node_id,
                                                self._headers())
                content = body.get("content") or body
                status = content.get("processing_status")
                if status == "complete":
                    item["status"] = "complete"; break
                if status == "failed":
                    raise ProviderError(self.provider, "PROCESSING_FAILED", str(content))
                time.sleep(poll_seconds)
            else:
                raise ProviderError(self.provider, "PROCESSING_TIMEOUT", node_id)
        return seeded

    @staticmethod
    def local_claim_check(claim, passages):
        """A deterministic safety check, not a claimed Senso verification."""
        text = " ".join(p.get("chunk_text", "") for p in passages).lower()
        claim_terms = [x for x in claim.lower().split() if len(x) > 3]
        if not claim_terms:
            return {"verdict": "unsupported", "check_id": "local-empty-claim"}
        hits = sum(1 for term in claim_terms if term in text)
        verdict = "supported" if hits == len(claim_terms) else ("unsupported" if hits == 0 else "conflicting")
        return {"verdict": verdict, "check_id": "local-constraint-check",
                "matched_terms": hits, "claim_terms": len(claim_terms)}


class FiredrillRunner:
    """Truthful Firedrill integration point for the OAuth/MCP runner workflow."""

    provider = "firedrill"

    def status(self):
        return {"provider": self.provider, "status": "not_configured",
                "message": "Authorize Firedrill MCP/CLI before executing a real drill"}

    def recovery_scenario(self, base_url):
        return {"name": "relay-out-of-stock-at-checkout", "target": base_url,
                "expected": ["CHECKOUT_REJECTED", "REAPPROVAL_REQUIRED", "COMPLETED"],
                "checks": ["no invalid capture", "exactly one final capture", "audit contains failure and recovery"]}
