"""Health and version information. /version lets the canary probe confirm which revision answered."""

from __future__ import annotations

import os


def version_info() -> dict[str, str]:
    return {
        "service": "ecolinks",
        "sha": os.environ.get("GG_GIT_SHA", "dev"),
        # Set by AWS Lambda for each published version; "local" elsewhere.
        "revision": os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", "local"),
        "region": os.environ.get("AWS_REGION", "local"),
    }
