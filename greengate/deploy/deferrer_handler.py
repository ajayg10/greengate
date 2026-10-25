"""AWS Lambda `gg-deferrer`: invoked by a one-time EventBridge Scheduler schedule, it re-triggers the GitLab
pipeline for the deferred ref with GG_DEFERRED=true. Standard library + boto3 (bundled in the Lambda runtime).

The GitLab pipeline trigger token lives in SSM Parameter Store (SecureString) and is never logged.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

PARAM = os.environ.get("GG_TRIGGER_TOKEN_PARAM", "/greengate/gitlab-trigger-token")
API = os.environ.get("GITLAB_API_URL", "https://gitlab.com/api/v4")


def _token() -> str:
    import boto3

    return boto3.client("ssm").get_parameter(Name=PARAM, WithDecryption=True)["Parameter"]["Value"]


def build_request(event: dict, token: str) -> urllib.request.Request:
    project = urllib.parse.quote(str(event["project_id"]), safe="")
    form = {
        "token": token,
        "ref": event.get("ref", "main"),
        "variables[GG_DEFERRED]": "true",
        "variables[GG_FIRST_DEFERRED_AT]": event.get("first_deferred_at") or "",
        "variables[GG_DEFERRED_FROM]": str(event.get("deferred_from_pipeline", "")),
    }
    return urllib.request.Request(f"{API}/projects/{project}/trigger/pipeline",
                                  data=urllib.parse.urlencode(form).encode(), method="POST")


def handler(event, context):  # noqa: ARG001 (Lambda signature)
    req = build_request(event, _token())
    with urllib.request.urlopen(req, timeout=20) as res:  # noqa: S310 (fixed https URL)
        body = json.loads(res.read())
    out = {"pipeline_id": body.get("id"), "web_url": body.get("web_url"), "ref": event.get("ref")}
    print(json.dumps({"greengate": "deferred deploy re-triggered", **out}))
    return out
