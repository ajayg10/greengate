"""Carbon ledger in S3. One immutable JSON object per record (no read-modify-write races):

    s3://<bucket>/ledger/YYYY-MM-DD/<pipeline_id>-<kind>.json

plus small "state" objects (for example the stable Lambda version) under state/.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

LEDGER_PREFIX = "ledger/"
STATE_PREFIX = "state/"
KINDS = {"baseline", "optimized", "normal", "decision", "deploy", "rollback", "incident"}


def _client(s3: Any = None) -> Any:
    if s3 is not None:
        return s3
    import boto3

    return boto3.client("s3", region_name=os.environ.get("AWS_REGION_PRIMARY") or os.environ.get("AWS_REGION"))


class Ledger:
    def __init__(self, bucket: str | None = None, s3: Any = None) -> None:
        self.bucket = bucket or os.environ["GG_LEDGER_BUCKET"]
        self._s3 = s3

    @property
    def s3(self) -> Any:
        self._s3 = _client(self._s3)
        return self._s3

    def append(self, record: dict[str, Any]) -> str:
        kind = record.get("kind", "normal")
        if kind not in KINDS:
            raise ValueError(f"unknown ledger kind {kind!r}")
        record = {"created_at": datetime.now(UTC).isoformat(timespec="seconds"), **record}
        day = record["created_at"][:10]
        key = f"{LEDGER_PREFIX}{day}/{record.get('pipeline_id', 'na')}-{kind}.json"
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(record, sort_keys=True).encode(),
                           ContentType="application/json")
        return key

    def records(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": LEDGER_PREFIX}
            if token:
                kw["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kw)
            for obj in page.get("Contents", []):
                body = self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
                out.append(json.loads(body))
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return sorted(out, key=lambda r: r.get("created_at", ""))

    def recent(self, *, kind: str | None = None, ref: str | None = None, n: int = 5) -> list[dict[str, Any]]:
        rows = [r for r in self.records()
                if (kind is None or r.get("kind") == kind) and (ref is None or r.get("ref") == ref)]
        return rows[-n:]

    def get_state(self, name: str) -> dict[str, Any] | None:
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=f"{STATE_PREFIX}{name}.json")["Body"].read()
        except Exception as err:  # botocore NoSuchKey; keep boto types out of this module
            if "NoSuchKey" in type(err).__name__ or "NoSuchKey" in str(err) or "404" in str(err):
                return None
            raise
        return json.loads(body)

    def put_state(self, name: str, value: dict[str, Any]) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=f"{STATE_PREFIX}{name}.json",
                           Body=json.dumps(value, sort_keys=True).encode(), ContentType="application/json")
