"""Merge a verified bot MR with the bot token: auto-merge when the pipeline succeeds, pinned to the tested SHA.

Duo flows themselves cannot merge (GitLab blocks AI workflows from merging), so the pipeline does it, and
only after the guardrail policy and the carbon verdict passed. Failure mode: label needs-human, explain, stop.
"""

from __future__ import annotations

from greengate.gitlab_api import GitLab


def auto_merge(gl: GitLab, *, mr_iid: int, sha: str) -> str:
    """Sets auto-merge (merges as soon as the running pipeline succeeds). `sha` pins the reviewed commit."""
    res = gl.put(gl.p(f"/merge_requests/{mr_iid}/merge"), {
        "sha": sha,
        "should_remove_source_branch": True,
        "auto_merge": True,
        "merge_when_pipeline_succeeds": True,  # older name of the same option
    }) or {}
    return res.get("state") or "auto-merge set"


def needs_human(gl: GitLab, *, mr_iid: int, note: str) -> None:
    gl.put(gl.p(f"/merge_requests/{mr_iid}"), {"add_labels": "needs-human"})
    gl.post(gl.p(f"/merge_requests/{mr_iid}/notes"), {"body": note})
