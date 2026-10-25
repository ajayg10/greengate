"""Deploy a static folder to AWS Amplify Hosting (manual deployment, no Git connection needed).

    python scripts/amplify_deploy.py <folder> <amplify-app-id> <branch>
"""

from __future__ import annotations

import io
import sys
import time
import zipfile
from pathlib import Path

import boto3
import httpx


def zip_dir(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(folder.rglob("*")):
            if f.is_file() and not f.name.endswith((".py", ".pyc")) and "__pycache__" not in f.parts:
                zf.write(f, f.relative_to(folder).as_posix())
    return buf.getvalue()


def main(folder: str, app_id: str, branch: str) -> int:
    if not app_id:
        print("GG_AMPLIFY_APP_ID not set: skipping dashboard deploy")
        return 0
    amp = boto3.client("amplify")
    data = zip_dir(Path(folder))
    dep = amp.create_deployment(appId=app_id, branchName=branch)
    res = httpx.put(dep["zipUploadUrl"], content=data, headers={"Content-Type": "application/zip"}, timeout=60)
    res.raise_for_status()
    amp.start_deployment(appId=app_id, branchName=branch, jobId=dep["jobId"])
    for _ in range(60):
        status = amp.get_job(appId=app_id, branchName=branch, jobId=dep["jobId"])["job"]["summary"]["status"]
        if status in {"SUCCEED", "FAILED", "CANCELLED"}:
            break
        time.sleep(3)
    url = f"https://{branch}.{amp.get_app(appId=app_id)['app']['defaultDomain']}"
    print(f"Amplify deploy {dep['jobId']}: {status} -> {url} ({len(data) / 1024:.1f} KiB)")
    return 0 if status == "SUCCEED" else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
