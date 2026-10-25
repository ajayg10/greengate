"""Trigger the greengate-optimizer Duo flow from CI when a main pipeline wastes carbon.

Conditions (all must hold):
  - the pipeline has waste findings OR is over the carbon budget
  - no optimizer MR is open already (max_open_optimizer_mrs)
  - the commit does not come from a greengate-auto MR (loop guard)
  - this pipeline has not triggered the flow before (idempotency marker in the Control Room notes)
Action: post "@<flow user> ..." on the Control Room issue (mention trigger), or assign a fresh issue to the
flow's service account (assign trigger), depending on GG_TRIGGER_MODE.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from greengate.gitlab_api import GitLab

MAX_NOTE_CHARS = 6000


@dataclass
class TriggerDecision:
    fire: bool
    reason: str


def marker(pipeline_id: int | str) -> str:
    return f"<!-- greengate:optimize pipeline={pipeline_id} -->"


def decide(*, findings: list[dict[str, Any]], gco2e: float, budget_g: float, open_optimizer_mrs: int,
           max_open: int, commit_mr_labels: list[list[str]], loop_label: str, commit_author: str,
           bot_usernames: set[str], already_triggered: bool) -> TriggerDecision:
    if already_triggered:
        return TriggerDecision(False, "already triggered for this pipeline")
    if any(loop_label in labels for labels in commit_mr_labels):
        return TriggerDecision(False, f"loop guard: commit comes from a {loop_label} merge request")
    if commit_author and commit_author in bot_usernames:
        return TriggerDecision(False, "loop guard: commit authored by a GreenGate bot")
    if open_optimizer_mrs >= max_open:
        return TriggerDecision(False, f"{open_optimizer_mrs} optimizer MR(s) already open")
    actionable = [f for f in findings if f.get("estimated_saving_pct", 0) > 0]
    if not actionable and gco2e <= budget_g:
        return TriggerDecision(False, "no actionable waste and within budget")
    why = []
    if actionable:
        why.append(f"{len(actionable)} waste finding(s)")
    if gco2e > budget_g:
        why.append(f"over budget ({gco2e:.3f} g > {budget_g:.3f} g)")
    return TriggerDecision(True, ", ".join(why))


def compact_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ("id", "severity", "jobs", "evidence", "estimated_saving_pct", "fix")
    return [{k: f[k] for k in keep if k in f} for f in findings]


def mention_body(*, flow_user: str, pipeline_id: int | str, pipeline_url: str, report: dict[str, Any],
                 findings: list[dict[str, Any]], budget_g: float) -> str:
    t = report["total"]
    payload = {
        "pipeline_id": pipeline_id,
        "target_file": ".gitlab/ci/app.yml",
        "gco2e_total": round(t["gco2e_total"], 4),
        "runner_minutes": t["runner_minutes"],
        "budget_g": budget_g,
        "findings": compact_findings(findings),
    }
    body = (
        f"@{flow_user} optimize pipeline {pipeline_id}\n\n"
        f"Pipeline {pipeline_url} used **{t['gco2e_total']:.3f} gCO2e** "
        f"({t['runner_minutes']:.1f} runner minutes, budget {budget_g:.3f} g). "
        "Rewrite `.gitlab/ci/app.yml` to remove the waste below. Do not change test semantics.\n\n"
        f"```json\n{json.dumps(payload, indent=1)}\n```\n\n{marker(pipeline_id)}"
    )
    if len(body) > MAX_NOTE_CHARS:  # drop evidence detail first, never the instruction
        payload["findings"] = [{k: f[k] for k in ("id", "jobs", "estimated_saving_pct")} for f in findings]
        body = (f"@{flow_user} optimize pipeline {pipeline_id}\n\n```json\n{json.dumps(payload)}\n```\n\n"
                f"{marker(pipeline_id)}")
    return body[:MAX_NOTE_CHARS]


def gather(gl: GitLab, *, sha: str, control_iid: int, pipeline_id: int | str) -> dict[str, Any]:
    mrs = gl.get(gl.p(f"/repository/commits/{sha}/merge_requests"))
    commit = gl.get(gl.p(f"/repository/commits/{sha}"))
    open_opt = gl.get(gl.p("/merge_requests"), state="opened", labels="greengate-optimizer", per_page=20)
    notes = list(gl.paginate(gl.p(f"/issues/{control_iid}/notes"), sort="desc"))
    return {
        "commit_mr_labels": [m.get("labels", []) for m in mrs],
        "commit_author": commit.get("author_name", ""),
        "commit_author_email": commit.get("author_email", ""),
        "open_optimizer_mrs": len(open_opt),
        "already_triggered": any(marker(pipeline_id) in (n.get("body") or "") for n in notes),
    }


def fire(gl: GitLab, *, mode: str, control_iid: int, body: str, flow_user_id: int | None, title: str) -> str:
    if mode == "assign":
        if not flow_user_id:
            raise ValueError("GG_TRIGGER_MODE=assign needs GG_OPTIMIZER_FLOW_USER_ID")
        issue = gl.post(gl.p("/issues"), {"title": title, "description": body, "labels": "greengate-auto",
                                          "assignee_ids": [flow_user_id]})
        return issue["web_url"]
    note = gl.post(gl.p(f"/issues/{control_iid}/notes"), {"body": body})
    return f"note {note.get('id')} on issue #{control_iid}"
