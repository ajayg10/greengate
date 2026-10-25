import json

import pytest

from greengate.bridge import policy, trigger, verdict
from greengate.report.render import STICKY_MARKER, sci_note, verdict_note
from greengate.sci.intensity import Intensity
from greengate.sci.meter import measure_jobs
from greengate.sci.waste import detect
from tests.fixtures import BASELINE_APP_YML, BASELINE_JOBS, OPTIMIZED_APP_YML

STATIC = Intensity("us-east1", 579.0, "static: test", "2026-10-25", live=False, verified=True)


@pytest.fixture()
def report():
    r = measure_jobs(BASELINE_JOBS, exclude_job_id=18, exclude_stages={".post"}, intensity_fn=lambda a, b: STATIC)
    r["findings"] = detect(BASELINE_APP_YML, ".gitlab/ci/app.yml", r["jobs"])
    return r


def base_kwargs(**over):
    kw = dict(findings=[{"id": "no_cache", "estimated_saving_pct": 12.0}], gco2e=1.0, budget_g=0.6,
              open_optimizer_mrs=0, max_open=1, commit_mr_labels=[], loop_label="greengate-auto",
              commit_author="Ajay", bot_usernames={"greengate-bot"}, already_triggered=False)
    kw.update(over)
    return kw


def test_trigger_fires_on_waste_or_budget():
    assert trigger.decide(**base_kwargs()).fire
    assert trigger.decide(**base_kwargs(findings=[], gco2e=1.0)).fire  # over budget only
    d = trigger.decide(**base_kwargs(findings=[{"id": "x", "estimated_saving_pct": 0}], gco2e=0.1))
    assert not d.fire and "within budget" in d.reason


@pytest.mark.parametrize("over,reason", [
    ({"already_triggered": True}, "already"),
    ({"commit_mr_labels": [["greengate-auto", "greengate-optimizer"]]}, "loop guard"),
    ({"commit_author": "greengate-bot"}, "bot"),
    ({"open_optimizer_mrs": 1}, "already open"),
])
def test_trigger_guards(over, reason):
    d = trigger.decide(**base_kwargs(**over))
    assert not d.fire and reason in d.reason


def test_mention_body_is_compact_and_parseable(report):
    body = trigger.mention_body(flow_user="ai-greengate-optimizer-x", pipeline_id=42, pipeline_url="https://p/42",
                                report=report, findings=report["findings"], budget_g=0.6)
    assert body.startswith("@ai-greengate-optimizer-x optimize pipeline 42")
    assert trigger.marker(42) in body
    payload = json.loads(body.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload["target_file"] == ".gitlab/ci/app.yml"
    assert {f["id"] for f in payload["findings"]} >= {"no_cache", "duplicate_job"}
    assert len(body) <= trigger.MAX_NOTE_CHARS


def test_policy_allows_optimizer_change_to_app_yml_only():
    ok = policy.evaluate(labels=["greengate-auto", "greengate-optimizer"], changed=[".gitlab/ci/app.yml"],
                         main_ci=BASELINE_APP_YML, mr_ci=OPTIMIZED_APP_YML)
    assert ok.ok, ok.errors
    bad = policy.evaluate(labels=["greengate-optimizer"], changed=[".gitlab/ci/app.yml", ".gitlab-ci.yml"],
                          main_ci=BASELINE_APP_YML, mr_ci=OPTIMIZED_APP_YML)
    assert not bad.ok and any(".gitlab-ci.yml" in e for e in bad.errors)


def test_policy_blocks_removed_or_weakened_tests():
    removed = OPTIMIZED_APP_YML.replace("    - pytest -q tests --cov=app\n", "    - echo skipped\n")
    res = policy.evaluate(labels=["greengate-optimizer"], changed=[".gitlab/ci/app.yml"],
                          main_ci=BASELINE_APP_YML, mr_ci=removed)
    assert not res.ok and "removed" in res.errors[0]
    weakened = OPTIMIZED_APP_YML.replace("pytest -q tests --cov=app", "pytest -q tests --cov=app || true")
    res = policy.evaluate(labels=["greengate-optimizer"], changed=[".gitlab/ci/app.yml"],
                          main_ci=BASELINE_APP_YML, mr_ci=weakened)
    assert not res.ok


def test_policy_invalid_yaml_and_phoenix_allowlist():
    res = policy.evaluate(labels=["greengate-optimizer"], changed=[".gitlab/ci/app.yml"],
                          main_ci=BASELINE_APP_YML, mr_ci="lint: [unclosed")
    assert not res.ok and "YAML" in res.errors[0]
    ph = policy.evaluate(labels=["phoenix", "greengate-auto"], changed=["app/main.py", "tests/test_x.py",
                         "postmortems/2026-10-25-x.md"], main_ci="", mr_ci=None)
    assert ph.ok
    ph_bad = policy.evaluate(labels=["phoenix"], changed=["app/main.py", ".gitlab/ci/greengate.yml"],
                             main_ci="", mr_ci=None)
    assert not ph_bad.ok
    human = policy.evaluate(labels=[], changed=["anything"], main_ci="", mr_ci=None)
    assert human.ok


def test_verdict_math():
    baselines = [{"app_gco2e": g, "app_runner_minutes": m} for g, m in [(1.0, 9.0), (1.2, 10.0), (1.1, 9.5)]]
    v = verdict.evaluate(candidate={"app_gco2e": 0.55, "app_runner_minutes": 4.75}, baselines=baselines,
                         tolerance_pct=10, failed_jobs=[], policy_ok=True)
    assert v.passed
    assert v.baseline_median_g == pytest.approx(1.1)
    assert v.gco2e_change_pct == pytest.approx(-50.0)
    assert v.duration_change_pct == pytest.approx(-50.0)
    small = verdict.evaluate(candidate={"app_gco2e": 1.05, "app_runner_minutes": 9}, baselines=baselines,
                             tolerance_pct=10, failed_jobs=[], policy_ok=True)
    assert not small.passed and "below the required" in small.reasons[0]
    failed = verdict.evaluate(candidate={"app_gco2e": 0.5, "app_runner_minutes": 4}, baselines=baselines,
                              tolerance_pct=10, failed_jobs=["unit-tests"], policy_ok=False)
    assert not failed.passed and len(failed.reasons) == 2
    none = verdict.evaluate(candidate={"app_gco2e": 0.5, "app_runner_minutes": 4}, baselines=[], tolerance_pct=10,
                            failed_jobs=[], policy_ok=True)
    assert not none.passed
    phoenix = verdict.evaluate(candidate={"app_gco2e": 1.3, "app_runner_minutes": 11}, baselines=baselines,
                               tolerance_pct=10, failed_jobs=[], policy_ok=True, require_improvement=False)
    assert phoenix.passed


def test_sci_note_snapshot(report):
    note = sci_note(report, budget_g=0.6, main_median_g=None, findings=report["findings"], pipeline_url="https://p/1")
    assert note.startswith(STICKY_MARKER)
    assert "### GreenGate SCI report" in note
    assert "| Budget | 0.600 g per pipeline: **over budget** |" in note
    assert "`duplicate_job`" in note and "`build-image`" in note
    assert "Grid intensity us-east1: 579 gCO2e/kWh" in note
    assert "Pipeline: https://p/1" in note


def test_verdict_notes():
    assert "Auto-merged by GreenGate:** -42.0% gCO2e" in verdict_note(passed=True, saving_pct=-42.0,
                                                                   duration_pct=-30.0, reasons=[])
    assert "needs-human" in verdict_note(passed=False, saving_pct=None, duration_pct=None, reasons=["x"])


def test_bot_mr_recognised_by_branch_without_labels():
    assert policy.kind_from_labels([], "greengate/optimize-123") == "optimizer"
    assert policy.kind_from_labels([], "greengate/phoenix-7") == "phoenix"
    assert policy.kind_from_labels([], "feature/x") is None
    res = policy.evaluate(labels=[], changed=[".gitlab-ci.yml"], main_ci=BASELINE_APP_YML, mr_ci=OPTIMIZED_APP_YML,
                          branch="greengate/optimize-9")
    assert not res.ok
