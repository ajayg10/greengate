"""Verdict for an optimizer MR: is it measurably greener than main's baseline, with everything green?"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Verdict:
    passed: bool
    gco2e_change_pct: float | None
    duration_change_pct: float | None
    baseline_median_g: float | None
    candidate_g: float
    reasons: list[str] = field(default_factory=list)


def pct_change(new: float, old: float) -> float:
    return 100.0 * (new - old) / old if old else 0.0


def evaluate(*, candidate: dict[str, Any], baselines: list[dict[str, Any]], tolerance_pct: float,
             failed_jobs: list[str], policy_ok: bool, require_improvement: bool = True) -> Verdict:
    """candidate/baselines are ledger records with app_gco2e and app_runner_minutes (like-for-like scope)."""
    reasons: list[str] = []
    if failed_jobs:
        reasons.append("failed jobs: " + ", ".join(failed_jobs))
    if not policy_ok:
        reasons.append("guardrail policy failed")
    cand_g = float(candidate["app_gco2e"])
    if len(baselines) < 1:
        reasons.append("no baseline runs on main in the ledger yet")
        return Verdict(False, None, None, None, cand_g, reasons)
    base_g = statistics.median(float(b["app_gco2e"]) for b in baselines)
    base_min = statistics.median(float(b["app_runner_minutes"]) for b in baselines)
    g_pct = pct_change(cand_g, base_g)
    d_pct = pct_change(float(candidate["app_runner_minutes"]), base_min)
    if require_improvement and -g_pct < tolerance_pct:
        reasons.append(f"carbon cut {-g_pct:.1f}% is below the required {tolerance_pct:.0f}%")
    return Verdict(not reasons, g_pct, d_pct, base_g, cand_g, reasons)
