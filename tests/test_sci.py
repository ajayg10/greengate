import httpx
import pytest

from greengate.sci import intensity as intensity_mod
from greengate.sci.intensity import Intensity, get_intensity
from greengate.sci.meter import job_footprint, load_coefficients, measure_jobs, runner_tag
from greengate.sci.waste import detect
from tests.fixtures import BASELINE_APP_YML, BASELINE_JOBS, OPTIMIZED_APP_YML

COEFF, SHA = load_coefficients()
STATIC_579 = Intensity("us-east1", 579.0, "static", "2026-10-25", live=False, verified=True)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("GG_FORCE_INTENSITY", raising=False)
    monkeypatch.delenv("ELECTRICITYMAPS_API_KEY", raising=False)
    intensity_mod.clear_cache()
    yield
    intensity_mod.clear_cache()


def test_every_coefficient_has_a_source():
    for section in ("sci", "compute", "memory", "pue", "embodied", "runners"):
        assert COEFF[section]["source"].startswith("http") or "http" in COEFF[section]["source"]
        assert "verified" in COEFF[section]
    for region, row in COEFF["grid_intensity"].items():
        assert row["source"] and row["accessed"] and "verified" in row, region


def test_embodied_per_vcpu_hour_matches_its_derivation():
    e = COEFF["embodied"]
    derived = e["total_embodied_kgco2e"] * 1000 / (e["lifespan_years"] * 8760 * e["largest_instance_vcpus"])
    assert e["g_per_vcpu_hour"] == pytest.approx(derived, rel=1e-3)


def test_runner_tag_from_tags_description_or_default():
    assert runner_tag({"tag_list": ["saas-linux-medium-amd64"]}, COEFF) == "saas-linux-medium-amd64"
    assert runner_tag({"tag_list": [], "runner": {"description": "1-x.saas-linux-small-arm64.y"}},
                      COEFF) == "saas-linux-small-arm64"
    assert runner_tag({"tag_list": ["docker"], "runner": None}, COEFF) == "saas-linux-small-amd64"


def test_job_footprint_by_hand():
    # 60 s on a small runner: 2 vCPU, 8 GB, 50% utilization, PUE 1.1, 579 g/kWh.
    fp = job_footprint({"name": "j", "duration": 60.0, "tag_list": []}, COEFF, STATIC_579, 0.5)
    hours = 60 / 3600
    avg_w = 0.71 + 0.5 * (4.26 - 0.71)
    kwh = (2 * hours * avg_w / 1000 + 8 * hours * 0.000392) * 1.1
    assert fp["kwh"] == pytest.approx(kwh)
    assert fp["gco2e_operational"] == pytest.approx(kwh * 579)
    assert fp["gco2e_embodied"] == pytest.approx(0.2958 * 2 * hours)
    assert fp["gco2e_total"] == pytest.approx(fp["gco2e_operational"] + fp["gco2e_embodied"])


def test_measure_excludes_meter_running_and_manual_jobs():
    report = measure_jobs(BASELINE_JOBS, exclude_job_id=18, exclude_stages={".post"},
                          intensity_fn=lambda r, c: STATIC_579)
    names = {j["name"] for j in report["jobs"]}
    assert names == {"lint", "unit-tests", "unit-tests-again", "integration-tests", "build-image", "gg-deploy-decide"}
    assert {s["name"] for s in report["skipped"]} == {"gg-smoke", "gg-sci-meter"}
    assert report["total"]["jobs"] == 6
    assert report["app"]["jobs"] == 5  # no deploy-stage or gg-* jobs in the like-for-like scope
    assert report["total"]["gco2e_total"] > report["app"]["gco2e_total"] > 0
    assert report["jobs"][0]["name"] == "build-image"  # sorted by carbon, biggest first
    assert report["coefficients_sha256"] == SHA
    assert report["method_version"] == "greengate-sci-1.0"


def test_waste_detectors_on_baseline():
    report = measure_jobs(BASELINE_JOBS, exclude_job_id=18, intensity_fn=lambda r, c: STATIC_579)
    findings = {f["id"]: f for f in detect(BASELINE_APP_YML, ".gitlab/ci/app.yml", report["jobs"])}
    assert set(findings) == {"no_cache", "heavy_image", "duplicate_job", "docker_no_layer_cache",
                             "no_rules_changes", "sequential_without_needs"}
    assert findings["duplicate_job"]["jobs"] == ["unit-tests-again"]
    assert findings["duplicate_job"]["evidence"] == [".gitlab/ci/app.yml:16"]
    assert findings["no_cache"]["estimated_saving_pct"] > 0
    assert findings["sequential_without_needs"]["estimated_saving_pct"] == 0.0
    assert "docs-only" in findings["no_rules_changes"]["conditional_saving"]


def test_waste_detectors_clean_on_optimized():
    report = measure_jobs(BASELINE_JOBS[:5], intensity_fn=lambda r, c: STATIC_579)
    assert detect(OPTIMIZED_APP_YML, ".gitlab/ci/app.yml", report["jobs"]) == []


def test_intensity_static_forced_and_live(monkeypatch):
    s = get_intensity("eu-north-1", COEFF, api_key="")
    assert s.live is False and s.g_per_kwh == 35 and s.verified is False
    intensity_mod.clear_cache()
    monkeypatch.setenv("GG_FORCE_INTENSITY", "420")
    forced = get_intensity("eu-north-1", COEFF, allow_force=True)
    assert forced.g_per_kwh == 420 and "demo override" in forced.source
    intensity_mod.clear_cache()
    assert get_intensity("us-east1", COEFF, api_key="").g_per_kwh == 579  # SCI measurements ignore the override
    monkeypatch.delenv("GG_FORCE_INTENSITY")

    def handler(request):
        assert request.headers["auth-token"] == "k"
        assert request.url.params["zone"] == "SE-SE3"
        return httpx.Response(200, json={"carbonIntensity": 21, "datetime": "2026-10-25T10:00:00Z"})

    live = get_intensity("eu-north-1", COEFF, api_key="k", client=httpx.Client(transport=httpx.MockTransport(handler)),
                         use_cache=False)
    assert live.live and live.g_per_kwh == 21 and "Electricity Maps" in live.source


def test_intensity_falls_back_when_api_fails():
    def boom(request):
        return httpx.Response(500)

    r = get_intensity("us-east1", COEFF, api_key="k", client=httpx.Client(transport=httpx.MockTransport(boom)),
                      use_cache=False)
    assert r.live is False and r.g_per_kwh == 579


def test_unknown_region_raises():
    with pytest.raises(KeyError):
        get_intensity("mars-1", COEFF, api_key="", use_cache=False)
