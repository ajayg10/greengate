"""Turn the jobs of a GitLab pipeline into energy (kWh) and carbon (gCO2e), following SCI.

Per job:  E = (vCPU * h * avgW / 1000 + memGB * h * kWh_per_GBh) * PUE
          I = grid intensity of the runner region
          M = embodied g per vCPU-hour * vCPU * h
          SCI_job = E * I + M      (functional unit R = one pipeline run, so the pipeline SCI is the sum)
"""

from __future__ import annotations

import hashlib
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from greengate.sci.intensity import Intensity, get_intensity

COEFFICIENTS_PATH = Path(__file__).with_name("coefficients.yaml")
MEASURED_STATUSES = {"success", "failed", "canceled"}
RUNNER_TAG_RE = re.compile(r"saas-linux-[a-z0-9]+-(?:amd64|arm64)")


def load_coefficients(path: Path = COEFFICIENTS_PATH) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    return yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()


def runner_tag(job: dict[str, Any], coefficients: dict[str, Any]) -> str:
    sizes = coefficients["runners"]["sizes"]
    for tag in job.get("tag_list") or []:
        if tag in sizes:
            return tag
    desc = (job.get("runner") or {}).get("description") or ""
    m = RUNNER_TAG_RE.search(desc)
    if m and m.group(0) in sizes:
        return m.group(0)
    return coefficients["runners"]["default_tag"]


def job_footprint(
    job: dict[str, Any], coefficients: dict[str, Any], intensity: Intensity, utilization: float
) -> dict[str, Any]:
    tag = runner_tag(job, coefficients)
    size = coefficients["runners"]["sizes"][tag]
    duration_s = float(job.get("duration") or 0.0)
    hours = duration_s / 3600.0
    c = coefficients["compute"]
    avg_w = c["min_watts_per_vcpu"] + utilization * (c["max_watts_per_vcpu"] - c["min_watts_per_vcpu"])
    cpu_kwh = size["vcpu"] * hours * avg_w / 1000.0
    mem_kwh = size["memory_gb"] * hours * coefficients["memory"]["kwh_per_gb_hour"]
    kwh = (cpu_kwh + mem_kwh) * coefficients["pue"]["value"]
    operational = kwh * intensity.g_per_kwh
    embodied = coefficients["embodied"]["g_per_vcpu_hour"] * size["vcpu"] * hours
    return {
        "id": job.get("id"),
        "name": job.get("name"),
        "stage": job.get("stage"),
        "status": job.get("status"),
        "runner_tag": tag,
        "vcpu": size["vcpu"],
        "memory_gb": size["memory_gb"],
        "region": size["region"],
        "duration_s": round(duration_s, 2),
        "queued_s": round(float(job.get("queued_duration") or 0.0), 2),
        "kwh": kwh,
        "gco2e_operational": operational,
        "gco2e_embodied": embodied,
        "gco2e_total": operational + embodied,
    }


def measure_jobs(
    jobs: list[dict[str, Any]],
    *,
    exclude_job_id: int | None = None,
    exclude_stages: set[str] | None = None,
    utilization: float | None = None,
    coefficients: dict[str, Any] | None = None,
    coefficients_sha: str | None = None,
    intensity_fn=get_intensity,
) -> dict[str, Any]:
    if coefficients is None:
        coefficients, coefficients_sha = load_coefficients()
    utilization = coefficients["compute"]["default_utilization"] if utilization is None else utilization
    exclude_stages = exclude_stages or set()
    measured, skipped = [], []
    intensities: dict[str, Intensity] = {}
    for job in jobs:
        if job.get("id") == exclude_job_id or job.get("stage") in exclude_stages:
            skipped.append({"name": job.get("name"), "reason": "the meter itself or a later stage"})
            continue
        if job.get("status") not in MEASURED_STATUSES or job.get("duration") is None:
            skipped.append({"name": job.get("name"), "reason": f"status {job.get('status')}, no runtime"})
            continue
        region = coefficients["runners"]["sizes"][runner_tag(job, coefficients)]["region"]
        if region not in intensities:
            intensities[region] = intensity_fn(region, coefficients)
        measured.append(job_footprint(job, coefficients, intensities[region], utilization))

    app_jobs = [j for j in measured if is_app_job(j)]
    return {
        "method_version": coefficients["method_version"],
        "coefficients_sha256": coefficients_sha,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "functional_unit": coefficients["sci"]["functional_unit_R"],
        "total": totals(measured),
        # Like-for-like scope used to compare MR and main pipelines: the app's lint/test/build jobs only
        # (no deploy jobs, no GreenGate machinery jobs, which differ between MR and main pipelines).
        "app": totals(app_jobs),
        "jobs": sorted(measured, key=lambda j: j["gco2e_total"], reverse=True),
        "skipped": skipped,
        "assumptions": {
            "cpu_utilization": utilization,
            "runner_region_note": coefficients["runners"]["region_note"],
            "embodied_mapping_verified": coefficients["embodied"].get("mapping_verified", False),
            "label": "estimate: job wall time x published coefficients, not a wattmeter reading",
        },
        "intensity": {r: i.to_dict() for r, i in intensities.items()},
        "sources": {
            "sci": coefficients["sci"]["source"],
            "compute": coefficients["compute"]["source"],
            "memory": coefficients["memory"]["source"],
            "pue": coefficients["pue"]["source"],
            "embodied": coefficients["embodied"]["source"],
            "runners": coefficients["runners"]["source"],
        },
    }


def is_app_job(job: dict[str, Any]) -> bool:
    return not str(job.get("name", "")).startswith("gg-") and job.get("stage") not in {"deploy", ".pre", ".post"}


def totals(measured: list[dict[str, Any]]) -> dict[str, Any]:
    t = {
        "jobs": len(measured),
        "duration_s": round(sum(j["duration_s"] for j in measured), 2),
        "runner_minutes": round(sum(j["duration_s"] for j in measured) / 60.0, 3),
        "kwh": sum(j["kwh"] for j in measured),
        "gco2e_operational": sum(j["gco2e_operational"] for j in measured),
        "gco2e_embodied": sum(j["gco2e_embodied"] for j in measured),
    }
    t["gco2e_total"] = t["gco2e_operational"] + t["gco2e_embodied"]
    return t


def median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None
