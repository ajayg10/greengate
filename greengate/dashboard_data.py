"""Ledger records -> the JSON the static dashboard renders. Measured values and projections stay separate."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

PIPELINE_KINDS = {"baseline", "optimized", "normal"}


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def build(records: list[dict[str, Any]]) -> dict[str, Any]:
    pipes = [r for r in records if r.get("kind") in PIPELINE_KINDS and "gco2e_total" in r]
    complete = [r for r in pipes if not r.get("failed_jobs")]  # incomplete runs never enter the medians
    base = [r for r in complete if r["kind"] == "baseline"]
    opt = [r for r in complete if r["kind"] == "optimized"]
    base_g = _median([float(r.get("app_gco2e", r["gco2e_total"])) for r in base])
    opt_g = _median([float(r.get("app_gco2e", r["gco2e_total"])) for r in opt])
    base_min = _median([float(r.get("app_runner_minutes", r.get("runner_minutes", 0))) for r in base])
    opt_min = _median([float(r.get("app_runner_minutes", r.get("runner_minutes", 0))) for r in opt])
    reduction = 100.0 * (base_g - opt_g) / base_g if base_g and opt_g is not None else None
    decisions = [r for r in records if r.get("kind") == "decision"]
    deploys = [r for r in records if r.get("kind") == "deploy"]
    rollbacks = [r for r in records if r.get("kind") == "rollback"]
    incidents = [r for r in records if r.get("kind") == "incident" and r.get("mttr_minutes") is not None]
    cf = [r for r in complete if r.get("gco2e_if_gitlab_hosted")]
    cf_actual = sum(float(r["gco2e_total"]) for r in cf)
    cf_hosted = sum(float(r["gco2e_if_gitlab_hosted"]) for r in cf)
    saved_per_run = (base_g - opt_g) if base_g is not None and opt_g is not None else None
    saved_total = saved_per_run * len(opt) if saved_per_run is not None else None
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "kpis": {
            "pipelines_measured": len(pipes),
            "gco2e_measured_total": round(sum(float(r["gco2e_total"]) for r in pipes), 4),
            "baseline_median_g": base_g,
            "optimized_median_g": opt_g,
            "reduction_pct": round(reduction, 1) if reduction is not None else None,
            "gco2e_saved_measured": round(saved_total, 4) if saved_total is not None else None,
            "saved_g_per_pipeline": round(saved_per_run, 4) if saved_per_run is not None else None,
            "baseline_median_minutes": base_min,
            "optimized_median_minutes": opt_min,
            "minutes_saved_per_pipeline": round(base_min - opt_min, 2) if base_min and opt_min is not None else None,
            "deploys_now": sum(1 for d in decisions if d.get("action") == "DEPLOY_NOW"),
            "deploys_deferred": sum(1 for d in decisions if d.get("action") == "DEFER"),
            "deploys_promoted": len(deploys),
            "rollbacks": len(rollbacks),
            "median_rollback_seconds": _median([float(r["rollback_seconds"]) for r in rollbacks
                                                if r.get("rollback_seconds") is not None]),
            "mttr_minutes": _median([float(r["mttr_minutes"]) for r in incidents]),
            # Counterfactual estimate: the same job minutes on GitLab hosted runners (Google Cloud us-east1).
            "runner_region_saving_pct": round(100.0 * (1 - cf_actual / cf_hosted), 1) if cf_hosted else None,
            "gco2e_if_gitlab_hosted_total": round(cf_hosted, 4) if cf_hosted else None,
        },
        "series": [{"t": r.get("created_at"), "g": round(float(r["gco2e_total"]), 4),
                    "app_g": round(float(r.get("app_gco2e", r["gco2e_total"])), 4), "kind": r["kind"],
                    "minutes": r.get("runner_minutes"), "pipeline": r.get("pipeline_id"), "url": r.get("web_url"),
                    "ref": r.get("ref"), "complete": not r.get("failed_jobs"),
                    "if_hosted_g": r.get("gco2e_if_gitlab_hosted")} for r in pipes],
        "decisions": [{"t": r.get("created_at"), "kind": r.get("kind"), "action": r.get("action"),
                       "reason": r.get("reason") or ("promoted" if r.get("kind") == "deploy" else "rolled back"),
                       "source": (r.get("intensity") or {}).get("source", ""),
                       "g_per_kwh": (r.get("intensity") or {}).get("g_per_kwh"),
                       "version": r.get("new_version"), "url": r.get("web_url")}
                      for r in sorted(decisions + deploys + rollbacks, key=lambda x: x.get("created_at", ""))][-25:],
    }
