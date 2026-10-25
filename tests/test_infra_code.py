import io
import json

import httpx
import pytest

from greengate.dashboard_data import build
from greengate.gitlab_api import GitLab, GitLabError
from greengate.ledger import Ledger


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body

    def list_objects_v2(self, Bucket, Prefix, **kw):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(f"NoSuchKey: {Key}")
        return {"Body": io.BytesIO(self.objects[Key])}


def test_ledger_append_read_and_state():
    s3 = FakeS3()
    led = Ledger("bucket", s3=s3)
    k1 = led.append({"kind": "baseline", "pipeline_id": 1, "ref": "main", "gco2e_total": 1.0})
    led.append({"kind": "optimized", "pipeline_id": 2, "ref": "main", "gco2e_total": 0.5})
    led.append({"kind": "normal", "pipeline_id": 3, "ref": "feature", "gco2e_total": 0.7})
    assert k1.startswith("ledger/") and k1.endswith("/1-baseline.json")
    assert [r["pipeline_id"] for r in led.recent(ref="main")] == [1, 2]
    assert [r["pipeline_id"] for r in led.recent(kind="baseline")] == [1]
    with pytest.raises(ValueError):
        led.append({"kind": "nonsense"})
    assert led.get_state("ecolinks-stable") is None
    led.put_state("ecolinks-stable", {"version": "4"})
    assert led.get_state("ecolinks-stable") == {"version": "4"}


def test_gitlab_client_retries_429_then_paginates():
    calls = {"n": 0}

    def handler(req: httpx.Request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        page = int(req.url.params.get("page", "1"))
        nxt = "2" if page == 1 else ""
        return httpx.Response(200, json=[{"id": page}], headers={"x-next-page": nxt})

    gl = GitLab(token="t", api_url="https://gl.example/api/v4", project="a/b",
                client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    assert [j["id"] for j in gl.paginate(gl.p("/pipelines/1/jobs"))] == [1, 2]
    assert gl.p() == "projects/a%2Fb"


def test_gitlab_client_raises_on_4xx():
    gl = GitLab(token="t", api_url="https://gl.example/api/v4", project=1,
                client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="no"))),
                sleep=lambda s: None)
    with pytest.raises(GitLabError) as err:
        gl.get("projects/1")
    assert err.value.status == 403


def test_dashboard_data_separates_measured_and_counts():
    records = [
        {"kind": "baseline", "gco2e_total": 1.2, "app_gco2e": 1.0, "app_runner_minutes": 9.0, "created_at": "1"},
        {"kind": "baseline", "gco2e_total": 1.3, "app_gco2e": 1.1, "app_runner_minutes": 10.0, "created_at": "2"},
        {"kind": "optimized", "gco2e_total": 0.6, "app_gco2e": 0.5, "app_runner_minutes": 4.0, "created_at": "3"},
        {"kind": "decision", "action": "DEFER", "reason": "dirty grid", "intensity": {"source": "s", "g_per_kwh": 420},
         "created_at": "4"},
        {"kind": "decision", "action": "DEPLOY_NOW", "reason": "clean", "intensity": {}, "created_at": "5"},
        {"kind": "deploy", "new_version": "4", "created_at": "6"},
        {"kind": "rollback", "rollback_seconds": 1.8, "created_at": "7"},
    ]
    data = build(records)
    k = data["kpis"]
    assert k["pipelines_measured"] == 3
    assert k["baseline_median_g"] == pytest.approx(1.05)
    assert k["reduction_pct"] == pytest.approx(52.4, abs=0.1)
    assert k["deploys_deferred"] == 1 and k["deploys_now"] == 1 and k["rollbacks"] == 1
    assert k["median_rollback_seconds"] == 1.8
    assert len(data["series"]) == 3 and len(data["decisions"]) == 4
    json.dumps(data)


def test_dashboard_data_empty_ledger():
    k = build([])["kpis"]
    assert k["reduction_pct"] is None and k["pipelines_measured"] == 0
