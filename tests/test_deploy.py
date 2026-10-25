import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from greengate.config import load_intent
from greengate.deploy import canary, decide, deferrer_handler, probe
from greengate.sci.intensity import Intensity

ROOT = Path(__file__).resolve().parents[1]
INTENT = load_intent(ROOT / "greengate.yml")
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def grid(values):
    return lambda region: Intensity(region, values[region], f"static {region}", "t", live=False, verified=False)


def test_urgent_label_deploys_now_even_on_dirty_grid():
    d = decide.decide(intent=INTENT, mr_labels=["security"], intensity_of=grid({"eu-north-1": 900, "us-west-2": 900}),
                      now=NOW)
    assert d.action == "DEPLOY_NOW" and d.urgent


def test_clean_grid_deploys_now():
    d = decide.decide(intent=INTENT, mr_labels=[], intensity_of=grid({"eu-north-1": 35, "us-west-2": 98}), now=NOW)
    assert d.action == "DEPLOY_NOW" and "below" in d.reason


def test_greener_alternative_is_recommended():
    d = decide.decide(intent=INTENT, mr_labels=[], intensity_of=grid({"eu-north-1": 400, "us-west-2": 80}), now=NOW)
    assert d.action == "DEPLOY_NOW" and d.recommend_region == "us-west-2"


def test_dirty_grid_defers_in_demo_mode():
    d = decide.decide(intent=INTENT, mr_labels=[], intensity_of=grid({"eu-north-1": 420, "us-west-2": 400}), now=NOW,
                      demo_mode=True)
    assert d.action == "DEFER"
    assert datetime.fromisoformat(d.defer_until) == NOW + timedelta(minutes=INTENT.demo_defer_minutes)
    assert "GG_DEPLOY_DECISION=DEFER" in d.to_env()


def test_max_deferral_ships_anyway():
    first = (NOW - timedelta(minutes=INTENT.max_defer_minutes + 1)).isoformat()
    d = decide.decide(intent=INTENT, mr_labels=[], intensity_of=grid({"eu-north-1": 420, "us-west-2": 400}), now=NOW,
                      deferred=True, first_deferred_at=first)
    assert d.action == "DEPLOY_NOW" and "anyway" in d.reason


def test_schedule_retrigger_payload():
    calls = {}

    class Sched:
        def create_schedule(self, **kw):
            calls.update(kw)

    d = decide.decide(intent=INTENT, mr_labels=[], intensity_of=grid({"eu-north-1": 420, "us-west-2": 400}), now=NOW,
                      demo_mode=True)
    name = decide.schedule_retrigger(Sched(), decision=d, group="gg-deferred-deploys", target_arn="arn:fn",
                                     role_arn="arn:role", project_id="88050627", ref="main", sha="a" * 40,
                                     pipeline_id="77")
    assert name == "gg-defer-77"
    assert calls["ScheduleExpression"] == "at(2026-10-25T12:03:00)"
    assert calls["ActionAfterCompletion"] == "DELETE"
    payload = json.loads(calls["Target"]["Input"])
    assert payload["ref"] == "main" and payload["first_deferred_at"]


def test_percentile_and_evaluate():
    assert probe.percentile([], 95) == 0.0
    assert probe.percentile(list(range(1, 101)), 95) == 95
    samples = [{"ok": True, "ms": 50.0}] * 19 + [{"ok": False, "ms": 2000.0}]
    r = probe.evaluate(samples, max_error_rate=0.02, max_p95_ms=1500, expected_revision="3", versions=["3", "3"])
    assert r.error_rate == pytest.approx(0.05) and r.version_ok
    assert not probe.slo_ok(r, max_error_rate=0.02, max_p95_ms=1500)
    wrong = probe.evaluate([{"ok": True, "ms": 1}], max_error_rate=0.02, max_p95_ms=1500, expected_revision="3",
                           versions=["2"])
    assert not wrong.version_ok


def _app(fail_resolve=False, revision="5"):
    links = {}

    def handler(req: httpx.Request):
        if req.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if req.url.path == "/version":
            return httpx.Response(200, json={"revision": revision})
        if req.method == "POST":
            code = f"Ab{len(links)}cde"
            links[code] = json.loads(req.content)["url"]
            return httpx.Response(201, json={"code": code})
        code = req.url.path.lstrip("/")
        if fail_resolve or code not in links:
            return httpx.Response(500)
        return httpx.Response(307, headers={"location": links[code]})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://canary.example")


def test_probe_run_healthy_and_broken():
    ok = probe.run("https://canary.example", requests=12, duration_s=0, max_error_rate=0.02, max_p95_ms=1500,
                   expected_revision="5", client=_app(), sleep=lambda s: None)
    assert ok.errors == 0 and ok.requests == 12 and ok.version_ok and ok.breach_at is None
    bad = probe.run("https://canary.example", requests=30, duration_s=0, max_error_rate=0.02, max_p95_ms=1500,
                    expected_revision="5", client=_app(fail_resolve=True), sleep=lambda s: None)
    assert bad.errors >= 1 and bad.breach_at is not None
    assert bad.requests < 30  # stopped early once the SLO could no longer be met


class FakeLambda:
    def __init__(self):
        self.version = 3
        self.aliases = {"live": {"FunctionVersion": "3", "RoutingConfig": {}}, "canary": {"FunctionVersion": "3"}}
        self.history = []
        self.env = {"ECOLINKS_TABLE": "t"}

    def update_function_code(self, **kw):
        self.history.append(("code", kw["ImageUri"]))

    def get_function_configuration(self, **kw):
        return {"LastUpdateStatus": "Successful", "State": "Active", "Environment": {"Variables": self.env}}

    def update_function_configuration(self, **kw):
        self.env = kw["Environment"]["Variables"]

    def publish_version(self, **kw):
        self.version += 1
        return {"Version": str(self.version)}

    def update_alias(self, **kw):
        self.aliases[kw["Name"]] = {"FunctionVersion": kw["FunctionVersion"], "RoutingConfig": kw["RoutingConfig"]}
        self.history.append(("alias", kw["Name"], kw["FunctionVersion"], json.dumps(kw["RoutingConfig"])))

    def get_alias(self, **kw):
        return self.aliases[kw["Name"]]


class FakeLedger:
    def __init__(self):
        self.state = {}

    def get_state(self, name):
        return self.state.get(name)

    def put_state(self, name, value):
        self.state[name] = value


def test_canary_promotes_on_healthy_probe():
    lam, ledger = FakeLambda(), FakeLedger()
    healthy = probe.ProbeResult(12, 0, 0.0, 80.0, 40.0, True)
    out = canary.run(lam=lam, ledger=ledger, function="ecolinks", image_uri="repo@sha256:x", sha="b" * 40,
                     steps=[10, 100], probe_fn=lambda v: healthy, slo_ok_fn=lambda r: True, sleep=lambda s: None)
    assert out.promoted and out.new_version == "4" and out.stable_version == "3"
    assert ("alias", "live", "3", json.dumps({"AdditionalVersionWeights": {"4": 0.1}})) in lam.history
    assert lam.aliases["live"] == {"FunctionVersion": "4", "RoutingConfig": {}}
    assert lam.aliases["canary"]["FunctionVersion"] == "4"
    assert ledger.state["ecolinks-stable"]["version"] == "4"
    assert lam.env["GG_GIT_SHA"] == "b" * 40


def test_canary_rolls_back_on_slo_breach():
    lam, ledger = FakeLambda(), FakeLedger()
    ledger.state["ecolinks-stable"] = {"version": "3", "sha": "a" * 40}
    t = iter([100.0, 101.5])
    broken = probe.ProbeResult(9, 3, 0.33, 90.0, 40.0, True, breach_at=99.0)
    out = canary.run(lam=lam, ledger=ledger, function="ecolinks", image_uri="repo@sha256:y", sha="c" * 40,
                     steps=[10, 100], probe_fn=lambda v: broken, slo_ok_fn=lambda r: False, sleep=lambda s: None,
                     clock=lambda: next(t))
    assert not out.promoted
    assert lam.aliases["live"] == {"FunctionVersion": "3", "RoutingConfig": {}}
    assert out.rollback_seconds == pytest.approx(1.0)
    assert ledger.state["ecolinks-stable"]["version"] == "3"


def test_bad_version_logs_filters_by_version():
    class Logs:
        def describe_log_streams(self, **kw):
            return {"logStreams": [{"logStreamName": "2026/10/25/[4]abc"}, {"logStreamName": "2026/10/25/[3]def"}]}

        def get_log_events(self, **kw):
            assert "[4]" in kw["logStreamName"]
            return {"events": [{"message": "Traceback...\n"}, {"message": "KeyError: 'Ab'\n"}]}

    assert canary.bad_version_logs(Logs(), function="ecolinks", version="4") == ["Traceback...", "KeyError: 'Ab'"]


def test_deferrer_builds_trigger_request():
    req = deferrer_handler.build_request({"project_id": "ajayg10/greengate", "ref": "main",
                                          "first_deferred_at": "2026-10-25T12:00:00+00:00",
                                          "deferred_from_pipeline": 77}, token="tkn")
    assert req.full_url == "https://gitlab.com/api/v4/projects/ajayg10%2Fgreengate/trigger/pipeline"
    body = req.data.decode()
    assert "token=tkn" in body and "variables%5BGG_DEFERRED%5D=true" in body


def test_canary_noop_when_version_is_already_live():
    lam, ledger = FakeLambda(), FakeLedger()
    lam.publish_version = lambda **kw: {"Version": "3"}  # unchanged code and config: Lambda returns the same version
    out = canary.run(lam=lam, ledger=ledger, function="ecolinks", image_uri="repo@sha256:x", sha="b" * 40,
                     steps=[10, 100], probe_fn=lambda v: pytest.fail("no probe expected"), slo_ok_fn=lambda r: True,
                     sleep=lambda s: None)
    assert out.promoted and out.new_version == out.stable_version == "3"
    assert not any(h[0] == "alias" for h in lam.history)
