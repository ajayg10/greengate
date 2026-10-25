"""Static waste detectors for the app pipeline (.gitlab/ci/app.yml), combined with measured job data.

Each finding: id, severity, evidence (file:line), job names, estimated_saving_pct (of the pipeline's gCO2e)
and a plain-language fix. Saving factors are documented estimates, not measurements; the optimizer MR's own
pipeline measures the real before/after.
"""

from __future__ import annotations

from typing import Any

import yaml

# Share of an affected job's footprint a fix is expected to remove (estimates; see docs/METHODOLOGY.md).
SAVING_FACTORS = {
    "no_cache": 0.30,             # pip downloads + wheel builds repeated in every job
    "heavy_image": 0.15,          # pulling python:3.12 (~1 GB) instead of python:3.12-slim (~45 MB)
    "duplicate_job": 1.00,        # the duplicate job disappears entirely
    "docker_no_layer_cache": 0.40,
    "no_rules_changes": 0.0,      # 0% on code changes; up to 100% on docs-only changes (reported separately)
    "sequential_without_needs": 0.0,  # saves wall-clock time, not energy: honest zero
}
RESERVED = {"stages", "variables", "default", "include", "workflow", "image", "services", "cache",
            "before_script", "after_script"}


def _line_index(text: str) -> dict[str, int]:
    """Top-level key -> 1-based line number."""
    node = yaml.compose(text)
    lines: dict[str, int] = {}
    if node is not None and isinstance(node, yaml.MappingNode):
        for key, _ in node.value:
            lines[key.value] = key.start_mark.line + 1
    return lines


def _script_text(job: dict[str, Any], default: dict[str, Any]) -> str:
    parts = []
    for key in ("before_script", "script"):
        val = job.get(key, default.get(key, []))
        parts.extend(val if isinstance(val, list) else [str(val)])
    return "\n".join(str(p) for p in parts)


def _image_name(job: dict[str, Any], default: dict[str, Any], top_image: Any) -> str:
    img = job.get("image", default.get("image", top_image))
    if isinstance(img, dict):
        img = img.get("name", "")
    return str(img or "")


def _resolve(name: str, data: dict[str, Any], seen: set[str] | None = None) -> dict[str, Any]:
    """Apply `extends:` (GitLab merges hashes deeply; a shallow merge is enough for these checks)."""
    seen = seen or set()
    job = data.get(name) or {}
    bases = job.get("extends") or []
    bases = [bases] if isinstance(bases, str) else list(bases)
    merged: dict[str, Any] = {}
    for base in bases:
        if base in data and base not in seen:
            merged.update(_resolve(base, data, seen | {name}))
    merged.update({k: v for k, v in job.items() if k != "extends"})
    return merged


def detect(app_yml_text: str, path: str, job_footprints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data = yaml.safe_load(app_yml_text) or {}
    lines = _line_index(app_yml_text)
    default = data.get("default", {}) or {}
    top_image = data.get("image")
    jobs = {k: _resolve(k, data) for k, v in data.items()
            if k not in RESERVED and not k.startswith(".") and isinstance(v, dict)}
    by_name = {j["name"]: j for j in job_footprints}
    total_g = sum(j["gco2e_total"] for j in job_footprints) or 0.0

    def saving(names: list[str], factor: float) -> float:
        if not total_g:
            return 0.0
        return round(100.0 * factor * sum(by_name[n]["gco2e_total"] for n in names if n in by_name) / total_g, 1)

    def ev(names: list[str]) -> list[str]:
        return [f"{path}:{lines.get(n, 1)}" for n in names]

    findings: list[dict[str, Any]] = []
    has_default_cache = "cache" in default or "cache" in data

    pip_jobs = [n for n, j in jobs.items() if "pip install" in _script_text(j, default)]
    no_cache = [n for n in pip_jobs if "cache" not in jobs[n] and not has_default_cache]
    if no_cache:
        findings.append({
            "id": "no_cache", "severity": "high", "jobs": no_cache, "evidence": ev(no_cache),
            "estimated_saving_pct": saving(no_cache, SAVING_FACTORS["no_cache"]),
            "fix": "Add a pip cache (cache: key from requirements files, paths: .cache/pip, PIP_CACHE_DIR).",
        })

    heavy = [n for n, j in jobs.items()
             if (img := _image_name(j, default, top_image)).startswith("python:") and "slim" not in img
             and "alpine" not in img]
    if heavy:
        findings.append({
            "id": "heavy_image", "severity": "medium", "jobs": heavy, "evidence": ev(heavy),
            "estimated_saving_pct": saving(heavy, SAVING_FACTORS["heavy_image"]),
            "fix": "Use python:3.12-slim for Python jobs.",
        })

    scripts: dict[str, list[str]] = {}
    for n, j in jobs.items():
        if j.get("script") is not None:
            scripts.setdefault(str(j.get("script")), []).append(n)
    dups = [names[1:] for names in scripts.values() if len(names) > 1]
    dup_names = [n for group in dups for n in group]
    if dup_names:
        findings.append({
            "id": "duplicate_job", "severity": "high", "jobs": dup_names, "evidence": ev(dup_names),
            "estimated_saving_pct": saving(dup_names, SAVING_FACTORS["duplicate_job"]),
            "fix": "Remove jobs whose script is identical to another job's (the tests still run once).",
        })

    docker = [n for n, j in jobs.items()
              if "docker build" in _script_text(j, default) and "--cache-from" not in _script_text(j, default)]
    if docker:
        findings.append({
            "id": "docker_no_layer_cache", "severity": "medium", "jobs": docker, "evidence": ev(docker),
            "estimated_saving_pct": saving(docker, SAVING_FACTORS["docker_no_layer_cache"]),
            "fix": "Pull the previous image and build with --cache-from (BuildKit inline cache).",
        })

    no_rules = [n for n, j in jobs.items() if not any("changes" in str(r) for r in (j.get("rules") or []))]
    if no_rules:
        findings.append({
            "id": "no_rules_changes", "severity": "medium", "jobs": no_rules, "evidence": ev(no_rules),
            "estimated_saving_pct": 0.0,
            "conditional_saving": "up to 100% of the pipeline for docs-only changes",
            "fix": "Add rules: changes: so docs-only changes skip lint, test, build and deploy.",
        })

    if len(jobs) > 1 and not any("needs" in j for j in jobs.values()):
        names = sorted(jobs)
        findings.append({
            "id": "sequential_without_needs", "severity": "low", "jobs": names, "evidence": ev(names[:1]),
            "estimated_saving_pct": 0.0,
            "note": "Parallel jobs finish sooner but use the same runner time, so energy stays the same.",
            "fix": "Use needs: so independent jobs (lint, unit tests) run in parallel.",
        })

    return sorted(findings, key=lambda f: f["estimated_saving_pct"], reverse=True)
