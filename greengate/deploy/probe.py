"""Synthetic SLO probe against the canary alias: create links, resolve them, check health and version."""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx


@dataclass
class ProbeResult:
    requests: int
    errors: int
    error_rate: float
    p95_ms: float
    p50_ms: float
    version_ok: bool
    samples: list[dict[str, Any]] = field(default_factory=list)
    breach_at: float | None = None  # time.time() when the SLO breach became certain

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["samples"] = d["samples"][:20]
        return d


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, math.ceil(pct / 100.0 * len(ordered)) - 1))
    return ordered[k]


def evaluate(samples: list[dict[str, Any]], *, max_error_rate: float, max_p95_ms: float,
             expected_revision: str | None, versions: list[str]) -> ProbeResult:
    lat = [s["ms"] for s in samples]
    errors = sum(1 for s in samples if not s["ok"])
    n = len(samples) or 1
    version_ok = expected_revision is None or (bool(versions) and all(v == expected_revision for v in versions))
    return ProbeResult(len(samples), errors, errors / n, percentile(lat, 95), percentile(lat, 50), version_ok,
                       samples)


def slo_ok(result: ProbeResult, *, max_error_rate: float, max_p95_ms: float) -> bool:
    return result.error_rate <= max_error_rate and result.p95_ms <= max_p95_ms and result.version_ok


def run(base_url: str, *, requests: int, duration_s: float, max_error_rate: float, max_p95_ms: float,
        expected_revision: str | None = None, client: httpx.Client | None = None, sleep=time.sleep) -> ProbeResult:
    """Each round: POST /links, GET /<code> (expect 307), GET /healthz. Two warm-up calls are not counted."""
    c = client or httpx.Client(timeout=10.0, follow_redirects=False)
    base = base_url.rstrip("/")
    for _ in range(2):  # cold start is a real but separate concern; the SLO is about warm behaviour
        try:
            c.get(f"{base}/healthz")
        except httpx.HTTPError:
            pass
    samples: list[dict[str, Any]] = []
    versions: list[str] = []
    rounds = max(1, requests // 3)
    gap = duration_s / rounds if rounds else 0
    allowed_errors = math.floor(max_error_rate * rounds * 3)
    breach_at = None

    def call(method: str, path: str, expect: set[int], **kw) -> httpx.Response | None:
        t0 = time.perf_counter()
        try:
            res = c.request(method, f"{base}{path}", **kw)
            ok = res.status_code in expect
        except httpx.HTTPError:
            res, ok = None, False
        samples.append({"op": f"{method} {path.split('?')[0][:20]}", "ok": ok,
                        "status": res.status_code if res is not None else 0,
                        "ms": round((time.perf_counter() - t0) * 1000, 1)})
        return res

    for i in range(rounds):
        created = call("POST", "/links", {201}, json={"url": f"https://example.org/greengate-probe/{i}"})
        code = None
        if created is not None and created.status_code == 201:
            code = created.json().get("code")
        call("GET", f"/{code or 'missingCode'}", {307})
        ver = call("GET", "/version", {200})
        if ver is not None and ver.status_code == 200:
            versions.append(str(ver.json().get("revision")))
        if breach_at is None and sum(1 for s in samples if not s["ok"]) > allowed_errors:
            breach_at = time.time()
            break  # the SLO can no longer be met: stop early so rollback starts sooner
        if gap and i < rounds - 1:
            sleep(gap)
    result = evaluate(samples, max_error_rate=max_error_rate, max_p95_ms=max_p95_ms,
                      expected_revision=expected_revision, versions=versions)
    if breach_at is None and not slo_ok(result, max_error_rate=max_error_rate, max_p95_ms=max_p95_ms):
        breach_at = time.time()
    result.breach_at = breach_at
    return result
