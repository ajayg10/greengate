"""Guardrail for bot merge requests, enforced by CI (not by trust).

- optimizer MRs may change only .gitlab/ci/app.yml
- phoenix MRs may change only app/**, tests/**, postmortems/**
- the set of pytest commands in app.yml must not shrink (tests can be de-duplicated, never removed or weakened)
- app.yml must stay valid YAML with at least one job
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

import yaml

ALLOWLISTS = {
    "optimizer": [".gitlab/ci/app.yml"],
    "phoenix": ["app/**", "tests/**", "postmortems/**"],
}
WEAKENING_FLAGS = ("--lf", "--last-failed", "-k ", "--deselect", "--maxfail", "|| true", "--exitfirst")


@dataclass
class PolicyResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


BRANCH_PREFIXES = {"greengate/optimize-": "optimizer", "greengate/phoenix-": "phoenix"}


def kind_from_labels(labels: list[str], branch: str = "") -> str | None:
    """Bot MRs are recognised by label OR by branch name, so a missing label can't skip the guardrail."""
    if "greengate-optimizer" in labels:
        return "optimizer"
    if "phoenix" in labels:
        return "phoenix"
    for prefix, kind in BRANCH_PREFIXES.items():
        if branch.startswith(prefix):
            return kind
    return None


def _match(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return fnmatch.fnmatch(path, pattern)


def check_paths(kind: str, changed: list[str]) -> list[str]:
    allowed = ALLOWLISTS[kind]
    return [f"path not allowed for {kind} MRs: {p}" for p in changed if not any(_match(p, a) for a in allowed)]


def pytest_commands(ci_text: str) -> set[str]:
    data = yaml.safe_load(ci_text) or {}
    cmds: set[str] = set()
    for job in data.values():
        if not isinstance(job, dict):
            continue
        for key in ("before_script", "script"):
            lines = job.get(key) or []
            for line in lines if isinstance(lines, list) else [lines]:
                if "pytest" in str(line):
                    cmds.add(" ".join(str(line).split()))
    return cmds


def check_tests(main_ci: str, mr_ci: str) -> list[str]:
    errors = []
    try:
        mr_data = yaml.safe_load(mr_ci)
    except yaml.YAMLError as err:
        return [f"app.yml is not valid YAML: {err}"]
    if not isinstance(mr_data, dict) or not any(isinstance(v, dict) and "script" in v for v in mr_data.values()):
        return ["app.yml has no jobs"]
    before, after = pytest_commands(main_ci), pytest_commands(mr_ci)
    missing = before - after
    if missing:
        errors.append("test commands removed or changed: " + "; ".join(sorted(missing)))
    for cmd in after - before:
        if any(flag in cmd for flag in WEAKENING_FLAGS):
            errors.append(f"test command weakened: {cmd}")
    return errors


def evaluate(*, labels: list[str], changed: list[str], main_ci: str, mr_ci: str | None,
             branch: str = "") -> PolicyResult:
    kind = kind_from_labels(labels, branch)
    if kind is None:
        return PolicyResult(True, notes=["not a GreenGate bot MR: policy not applied"])
    errors = check_paths(kind, changed)
    if kind == "optimizer":
        if mr_ci is None:
            errors.append(".gitlab/ci/app.yml missing on the MR branch")
        else:
            errors += check_tests(main_ci, mr_ci)
    return PolicyResult(not errors, errors, [f"{kind} allowlist: {', '.join(ALLOWLISTS[kind])}"])
