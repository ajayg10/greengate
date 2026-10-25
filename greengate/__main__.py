"""GreenGate CLI, used by the CI jobs in .gitlab/ci/greengate.yml.

    python -m greengate sci --out sci-report.json      measure this pipeline, ledger + sticky MR note
    python -m greengate bridge                          maybe trigger the optimizer flow (main only)
    python -m greengate policy                          guardrail for bot MRs (fails the job on violation)
    python -m greengate verdict                         carbon verdict + auto-merge for bot MRs
    python -m greengate decide                          carbon-aware deploy decision -> deploy.env
    python -m greengate canary                          Lambda canary, probe, promote or roll back
    python -m greengate dashboard-data --out FILE       ledger -> dashboard JSON
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from greengate.config import load_intent

APP_CI = ".gitlab/ci/app.yml"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def log(msg: str) -> None:
    print(f"[greengate] {msg}", flush=True)


def gitlab(require_token: bool = False):
    from greengate.gitlab_api import GitLab

    token = env("GITLAB_BOT_TOKEN")
    if require_token and not token:
        return None
    return GitLab(token=token)


def ledger_or_none():
    if not env("GG_LEDGER_BUCKET"):
        return None
    from greengate.ledger import Ledger

    return Ledger(env("GG_LEDGER_BUCKET"))


def profile_of(ci_text: str) -> str:
    for line in ci_text.splitlines()[:5]:
        if "greengate-profile:" in line:
            return line.split("greengate-profile:", 1)[1].strip().split()[0]
    return "unknown"


def read_json(path: str, default: Any = None) -> Any:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def write_json(path: str, data: Any) -> None:
    Path(path).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


# ---------------------------------------------------------------------------- sci
def cmd_sci(a: argparse.Namespace) -> int:
    from greengate.report.render import STICKY_MARKER, sci_note
    from greengate.sci.meter import measure_jobs, median_or_none
    from greengate.sci.waste import detect

    intent = load_intent()
    gl = gitlab()
    pipeline_id = int(a.pipeline or env("CI_PIPELINE_ID"))
    jobs = gl.pipeline_jobs(pipeline_id)
    me = int(env("CI_JOB_ID", "0") or 0)
    report = measure_jobs(jobs, exclude_job_id=me, exclude_stages={".post"})
    ci_text = Path(APP_CI).read_text(encoding="utf-8") if Path(APP_CI).exists() else ""
    findings = detect(ci_text, APP_CI, report["jobs"]) if ci_text else []
    report["findings"] = findings
    report["profile"] = profile_of(ci_text) if ci_text else "skipped"
    write_json(a.out, report)
    write_json("findings.json", findings)
    t = report["total"]
    log(f"pipeline {pipeline_id}: {t['gco2e_total']:.4f} gCO2e, {t['kwh'] * 1000:.3f} Wh, "
        f"{t['runner_minutes']:.2f} runner min, {len(findings)} findings, profile {report['profile']}")

    mr_iid = env("CI_MERGE_REQUEST_IID")
    ref = env("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME") or env("CI_COMMIT_REF_NAME")
    main_median = None
    ledger = ledger_or_none()
    if ledger:
        try:
            prior = ledger.recent(ref=env("CI_DEFAULT_BRANCH", "main"), n=50)
            prior = [r for r in prior if r.get("kind") in {"baseline", "optimized", "normal"}][-5:]
            main_median = median_or_none([float(r["gco2e_total"]) for r in prior])
            on_default = not mr_iid and ref == env("CI_DEFAULT_BRANCH", "main")
            deferred_only = env("GG_DEFERRED") == "true"
            kind = (report["profile"] if report["profile"] in {"baseline", "optimized"} else "normal") \
                if on_default and not deferred_only else "normal"
            if mr_iid:
                kind = "normal"
            key = ledger.append({
                "kind": kind, "pipeline_id": pipeline_id, "sha": env("CI_COMMIT_SHA"), "ref": ref,
                "mr_iid": mr_iid or None, "source": env("CI_PIPELINE_SOURCE"), "profile": report["profile"],
                "web_url": env("CI_PIPELINE_URL"), "gco2e_total": t["gco2e_total"], "kwh": t["kwh"],
                "gco2e_if_gitlab_hosted": t.get("gco2e_if_gitlab_hosted"),
                "duration_s": t["duration_s"], "runner_minutes": t["runner_minutes"],
                "app_gco2e": report["app"]["gco2e_total"], "app_runner_minutes": report["app"]["runner_minutes"],
                "findings_count": len(findings), "findings": [f["id"] for f in findings],
                # A run with failed jobs is incomplete: it is stored but never counted as baseline/optimized.
                "failed_jobs": [j["name"] for j in report["jobs"] if j["status"] == "failed"],
                "deferred": env("GG_DEFERRED") == "true",
            })
            log(f"ledger: s3://{ledger.bucket}/{key}")
        except Exception as err:  # the meter must never break the pipeline
            log(f"ledger skipped: {err}")

    note = sci_note(report, budget_g=intent.carbon_budget_g_per_pipeline, main_median_g=main_median,
                    findings=findings, pipeline_url=env("CI_PIPELINE_URL"))
    Path("sci-report.md").write_text(note, encoding="utf-8")
    if mr_iid and env("GITLAB_BOT_TOKEN"):
        try:
            notes = list(gl.paginate(gl.p(f"/merge_requests/{mr_iid}/notes")))
            sticky = next((n for n in notes if STICKY_MARKER in (n.get("body") or "")), None)
            if sticky:
                gl.put(gl.p(f"/merge_requests/{mr_iid}/notes/{sticky['id']}"), {"body": note})
            else:
                gl.post(gl.p(f"/merge_requests/{mr_iid}/notes"), {"body": note})
            log(f"sticky SCI note updated on !{mr_iid}")
        except Exception as err:
            log(f"MR note skipped: {err}")
    return 0


# ---------------------------------------------------------------------------- bridge
def cmd_bridge(a: argparse.Namespace) -> int:
    from greengate.bridge import trigger

    intent = load_intent()
    report = read_json(a.report)
    findings = read_json("findings.json", [])
    if not report:
        log("no sci-report.json: nothing to do")
        return 0
    gl = gitlab(require_token=True)
    if gl is None:
        log("GITLAB_BOT_TOKEN not set: bridge in dry-run mode")
        return 0
    control = int(env("GG_CONTROL_ISSUE_IID", "0") or 0)
    pipeline_id = env("CI_PIPELINE_ID")
    facts = trigger.gather(gl, sha=env("CI_COMMIT_SHA"), control_iid=control, pipeline_id=pipeline_id)
    bots = {u for u in env("GG_BOT_USERNAMES", "").split(",") if u}
    d = trigger.decide(findings=findings, gco2e=report["total"]["gco2e_total"],
                       budget_g=intent.carbon_budget_g_per_pipeline, open_optimizer_mrs=facts["open_optimizer_mrs"],
                       max_open=intent.max_open_optimizer_mrs, commit_mr_labels=facts["commit_mr_labels"],
                       loop_label=intent.loop_guard_label, commit_author=facts["commit_author"],
                       bot_usernames=bots, already_triggered=facts["already_triggered"])
    log(f"bridge decision: {'TRIGGER' if d.fire else 'skip'} ({d.reason})")
    if not d.fire:
        return 0
    flow_user = env("GG_OPTIMIZER_FLOW_USER")
    if not flow_user:
        log("GG_OPTIMIZER_FLOW_USER not set (flow not enabled yet): not triggering")
        return 0
    body = trigger.mention_body(flow_user=flow_user, pipeline_id=pipeline_id, pipeline_url=env("CI_PIPELINE_URL"),
                                report=report, findings=findings, budget_g=intent.carbon_budget_g_per_pipeline)
    uid = env("GG_OPTIMIZER_FLOW_USER_ID")
    where = trigger.fire(gl, mode=env("GG_TRIGGER_MODE", "mention"), control_iid=control, body=body,
                         flow_user_id=int(uid) if uid else None, title=f"Optimize pipeline {pipeline_id}")
    log(f"optimizer flow triggered: {where}")
    return 0


# ---------------------------------------------------------------------------- policy
def cmd_policy(a: argparse.Namespace) -> int:
    from greengate.bridge.policy import evaluate

    labels = [x for x in env("CI_MERGE_REQUEST_LABELS").split(",") if x]
    target = env("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "main")
    git("fetch", "--depth=50", "origin", target)
    changed = [p for p in git("diff", "--name-only", f"origin/{target}...HEAD").splitlines() if p]
    try:
        main_ci = git("show", f"origin/{target}:{APP_CI}")
    except subprocess.CalledProcessError:
        main_ci = ""
    mr_ci = Path(APP_CI).read_text(encoding="utf-8") if Path(APP_CI).exists() else None
    res = evaluate(labels=labels, changed=changed, main_ci=main_ci, mr_ci=mr_ci,
                   branch=env("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME"))
    write_json("policy.json", {"ok": res.ok, "errors": res.errors, "notes": res.notes, "changed": changed})
    for n in res.notes:
        log(n)
    for e in res.errors:
        log(f"POLICY VIOLATION: {e}")
    log("policy OK" if res.ok else "policy FAILED")
    return 0 if res.ok else 1


# ---------------------------------------------------------------------------- verdict
def cmd_verdict(a: argparse.Namespace) -> int:
    from greengate.bridge import automerge, verdict
    from greengate.bridge.policy import kind_from_labels
    from greengate.report.render import verdict_note

    intent = load_intent()
    labels = [x for x in env("CI_MERGE_REQUEST_LABELS").split(",") if x]
    kind = kind_from_labels(labels, env("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME"))
    if kind is None:
        log("not a GreenGate bot MR: no verdict")
        return 0
    report = read_json(a.report) or {}
    policy = read_json("policy.json", {"ok": False, "errors": ["policy.json missing"]})
    gl = gitlab()
    jobs = gl.pipeline_jobs(int(env("CI_PIPELINE_ID")))
    failed = [j["name"] for j in jobs if j.get("status") == "failed" and not j.get("allow_failure")
              and j.get("name") != env("CI_JOB_NAME")]
    ledger = ledger_or_none()
    baselines = []
    if ledger:
        rows = [r for r in ledger.recent(kind="baseline", ref=env("CI_DEFAULT_BRANCH", "main"), n=50)
                if not r.get("failed_jobs")]
        baselines = rows[-3:]
    candidate = {"app_gco2e": report.get("app", {}).get("gco2e_total", 0.0),
                 "app_runner_minutes": report.get("app", {}).get("runner_minutes", 0.0)}
    v = verdict.evaluate(candidate=candidate, baselines=baselines, tolerance_pct=intent.sci_regression_tolerance_pct,
                         failed_jobs=failed, policy_ok=bool(policy.get("ok")),
                         require_improvement=(kind == "optimizer"))
    write_json("verdict.json", v.__dict__)
    log(f"verdict: {'PASS' if v.passed else 'FAIL'} change={v.gco2e_change_pct} reasons={v.reasons}")
    mr_iid = int(env("CI_MERGE_REQUEST_IID"))
    if not env("GITLAB_BOT_TOKEN"):
        log("GITLAB_BOT_TOKEN not set: cannot merge or label, stopping here")
        return 0 if v.passed else 1
    bot = gitlab(require_token=True)
    if v.passed:
        state = automerge.auto_merge(bot, mr_iid=mr_iid, sha=env("CI_COMMIT_SHA"))
        bot.post(bot.p(f"/merge_requests/{mr_iid}/notes"),
                 {"body": verdict_note(passed=True, saving_pct=v.gco2e_change_pct or 0.0,
                                       duration_pct=v.duration_change_pct or 0.0, reasons=[])})
        log(f"auto-merge requested: {state}")
        return 0
    automerge.needs_human(bot, mr_iid=mr_iid, note=verdict_note(passed=False, saving_pct=v.gco2e_change_pct,
                                                                 duration_pct=v.duration_change_pct,
                                                                 reasons=v.reasons))
    return 1


# ---------------------------------------------------------------------------- decide
def cmd_decide(a: argparse.Namespace) -> int:
    from greengate.deploy.decide import decide, schedule_retrigger
    from greengate.sci.intensity import get_intensity
    from greengate.sci.meter import load_coefficients

    intent = load_intent()
    coefficients, _ = load_coefficients()
    sha = env("CI_COMMIT_SHA")
    labels: list[str] = []
    gl = gitlab()
    try:
        for mr in gl.get(gl.p(f"/repository/commits/{sha}/merge_requests")):
            labels += mr.get("labels", [])
    except Exception as err:
        log(f"could not read merged MR labels: {err}")
    image_exists = True
    if a.check_image:
        import boto3

        repo = env("ECR_REPOSITORY_URI").split("/", 1)[1]
        try:
            boto3.client("ecr").describe_images(repositoryName=repo, imageIds=[{"imageTag": sha}])
        except Exception:
            image_exists = False
    if not image_exists:
        Path("deploy.env").write_text("GG_DEPLOY_DECISION=NOTHING_TO_DEPLOY\nGG_DECISION_REASON=no image was built "
                                      "for this commit (docs-only change)\n", encoding="utf-8")
        log("nothing to deploy: no image for this commit")
        return 0
    d = decide(intent=intent, mr_labels=labels, intensity_of=lambda r: get_intensity(r, coefficients, allow_force=True),
               deferred=env("GG_DEFERRED") == "true", first_deferred_at=env("GG_FIRST_DEFERRED_AT") or None,
               demo_mode=env("GG_DEMO_MODE") == "true")
    Path("deploy.env").write_text(d.to_env(), encoding="utf-8")
    write_json("decision.json", d.to_dict())
    log(f"decision: {d.action} in {d.region}: {d.reason} [intensity source: {d.intensity['source']}]")
    if d.action == "DEFER":
        import boto3

        name = schedule_retrigger(boto3.client("scheduler"), decision=d, group=env("GG_SCHEDULE_GROUP"),
                                  target_arn=env("GG_DEFERRER_ARN"), role_arn=env("GG_SCHEDULER_ROLE_ARN"),
                                  project_id=env("CI_PROJECT_ID"), ref=env("CI_COMMIT_REF_NAME"), sha=sha,
                                  pipeline_id=env("CI_PIPELINE_ID"))
        log(f"scheduled re-trigger {name} at {d.defer_until}")
    ledger = ledger_or_none()
    if ledger:
        ledger.append({"kind": "decision", "pipeline_id": env("CI_PIPELINE_ID"), "sha": sha,
                       "ref": env("CI_COMMIT_REF_NAME"), "web_url": env("CI_PIPELINE_URL"), **d.to_dict()})
    if env("GITLAB_BOT_TOKEN"):
        try:
            bot = gitlab(require_token=True)
            icon = {"DEPLOY_NOW": "Deploying now", "DEFER": "Deploy deferred"}[d.action]
            bot.post(bot.p(f"/repository/commits/{sha}/comments"),
                     {"note": f"**GreenGate: {icon}.** {d.reason}. Intensity source: {d.intensity['source']}."})
        except Exception as err:
            log(f"commit note skipped: {err}")
    return 0


# ---------------------------------------------------------------------------- canary
def cmd_canary(a: argparse.Namespace) -> int:
    import boto3

    from greengate.deploy import canary, probe

    decision = env("GG_DEPLOY_DECISION")
    if decision != "DEPLOY_NOW":
        log(f"canary skipped: decision is {decision or 'missing'} ({env('GG_DECISION_REASON')})")
        return 0
    intent = load_intent()
    function = env("GG_FUNCTION_NAME", "ecolinks")
    sha = env("CI_COMMIT_SHA")
    lam = boto3.client("lambda")
    ecr = boto3.client("ecr")
    repo = env("ECR_REPOSITORY_URI").split("/", 1)[1]
    digest = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": sha}])["imageDetails"][0]["imageDigest"]
    image_uri = f"{env('ECR_REPOSITORY_URI')}@{digest}"
    canary_url = lam.get_function_url_config(FunctionName=function, Qualifier="canary")["FunctionUrl"]
    live_url = lam.get_function_url_config(FunctionName=function, Qualifier="live")["FunctionUrl"]
    ledger = ledger_or_none()

    def probe_fn(version: str):
        r = probe.run(canary_url, requests=intent.probe.requests, duration_s=intent.probe.duration_s,
                      max_error_rate=intent.slo.max_error_rate, max_p95_ms=intent.slo.max_p95_ms,
                      expected_revision=version)
        log(f"probe v{version}: {r.requests} req, error rate {r.error_rate:.1%}, p95 {r.p95_ms:.0f} ms, "
            f"version_ok={r.version_ok}")
        return r

    out = canary.run(lam=lam, ledger=ledger, function=function, image_uri=image_uri, sha=sha,
                     steps=intent.canary_steps, probe_fn=probe_fn,
                     slo_ok_fn=lambda r: probe.slo_ok(r, max_error_rate=intent.slo.max_error_rate,
                                                      max_p95_ms=intent.slo.max_p95_ms))
    write_json("canary.json", {**out.to_dict(), "live_url": live_url, "canary_url": canary_url})
    rec = {"pipeline_id": env("CI_PIPELINE_ID"), "sha": sha, "ref": env("CI_COMMIT_REF_NAME"),
           "web_url": env("CI_PIPELINE_URL"), "function": function, **out.to_dict()}
    rec.pop("steps", None)
    if out.promoted and out.new_version == out.stable_version:
        log(f"v{out.new_version} is already live with this image: nothing to deploy")
        return 0
    if out.promoted:
        log(f"PROMOTED v{out.new_version} to 100% at {live_url}")
        if ledger:
            ledger.append({"kind": "deploy", **rec})
        return 0
    log(f"ROLLED BACK to v{out.stable_version} in {out.rollback_seconds}s (SLO breach on v{out.new_version})")
    logs = canary.bad_version_logs(boto3.client("logs"), function=function, version=out.new_version)
    stable_sha = (ledger.get_state(f"{function}-stable") or {}).get("sha", "") if ledger else ""
    rollback = {**rec, "bad_version": out.new_version, "stable_version": out.stable_version,
                "commit_range": f"{stable_sha[:12]}..{sha[:12]}", "stable_sha": stable_sha, "log_tail": logs,
                "job_url": env("CI_JOB_URL")}
    write_json("rollback.json", rollback)
    if ledger:
        ledger.append({"kind": "rollback", **{k: v for k, v in rollback.items() if k != "log_tail"}})
    if env("GITLAB_BOT_TOKEN"):
        try:
            from greengate.bridge.incident import open_incident

            url = open_incident(gitlab(require_token=True), rollback=rollback,
                                phoenix_user=env("GG_PHOENIX_FLOW_USER"))
            log(f"incident opened: {url}")
        except Exception as err:
            log(f"incident issue skipped: {err}")
    return 1  # a rollback is a failed deploy: keep it visible in the pipeline


# ---------------------------------------------------------------------------- dashboard
def cmd_dashboard_data(a: argparse.Namespace) -> int:
    from greengate.dashboard_data import build

    ledger = ledger_or_none()
    records = ledger.records() if ledger else read_json(a.from_file, [])
    write_json(a.out, build(records))
    log(f"dashboard data: {len(records)} ledger records -> {a.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="greengate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sci")
    s.add_argument("--pipeline", default="")
    s.add_argument("--out", default="sci-report.json")
    s.set_defaults(fn=cmd_sci)
    b = sub.add_parser("bridge")
    b.add_argument("--report", default="sci-report.json")
    b.set_defaults(fn=cmd_bridge)
    sub.add_parser("policy").set_defaults(fn=cmd_policy)
    v = sub.add_parser("verdict")
    v.add_argument("--report", default="sci-report.json")
    v.set_defaults(fn=cmd_verdict)
    d = sub.add_parser("decide")
    d.add_argument("--check-image", action="store_true")
    d.set_defaults(fn=cmd_decide)
    sub.add_parser("canary").set_defaults(fn=cmd_canary)
    dd = sub.add_parser("dashboard-data")
    dd.add_argument("--out", default="dashboard/data.json")
    dd.add_argument("--from-file", default="")
    dd.set_defaults(fn=cmd_dashboard_data)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
