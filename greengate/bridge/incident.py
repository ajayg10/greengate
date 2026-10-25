"""On rollback: open an incident issue with the evidence and hand it to the phoenix-responder Duo flow."""

from __future__ import annotations

from typing import Any

from greengate.gitlab_api import GitLab


def incident_body(rollback: dict[str, Any]) -> str:
    probe = rollback.get("probe") or {}
    logs = "\n".join((rollback.get("log_tail") or [])[-30:]) or "(no log lines captured)"
    return "\n".join([
        f"Canary for `{rollback.get('sha', '')[:12]}` breached the SLO and GreenGate rolled back automatically.",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Bad Lambda version | {rollback.get('bad_version')} |",
        f"| Restored stable version | {rollback.get('stable_version')} |",
        f"| Commit range (stable..bad) | `{rollback.get('commit_range')}` |",
        f"| Probe error rate | {probe.get('error_rate', 0):.1%} over {probe.get('requests', 0)} requests |",
        f"| Probe p95 | {probe.get('p95_ms', 0):.0f} ms |",
        f"| Rollback time | {rollback.get('rollback_seconds')} s from SLO breach to traffic restored |",
        f"| Pipeline | {rollback.get('web_url', '')} |",
        f"| Canary job | {rollback.get('job_url', '')} |",
        "",
        "Failing probe samples:",
        "",
        "```json",
        str([s for s in probe.get("samples", []) if not s.get("ok")][:10]),
        "```",
        "",
        "Last log lines of the bad version:",
        "",
        "```",
        logs[-4000:],
        "```",
    ])


def open_incident(gl: GitLab, *, rollback: dict[str, Any], phoenix_user: str = "") -> str:
    issue = gl.post(gl.p("/issues"), {
        "title": f"Incident: canary failed for {rollback.get('sha', '')[:8]}",
        "description": incident_body(rollback),
        "labels": "incident,phoenix,greengate-auto",
    })
    if phoenix_user:
        gl.post(gl.p(f"/issues/{issue['iid']}/notes"), {
            "body": f"@{phoenix_user} investigate this incident: find the root cause in "
                    f"`{rollback.get('commit_range')}`, open a fix MR with a regression test (or a revert MR "
                    "if unsure) and write the postmortem."})
    return issue["web_url"]
