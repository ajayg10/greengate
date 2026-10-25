# GreenGate results

Only measured values from real runs appear here, each with a link. SCI grams are estimates by method
(docs/METHODOLOGY.md); percentages compare the same jobs with the same method.

## Verified so far (2026-10-25)

| What | Result | Evidence |
|---|---|---|
| App live on AWS Lambda | `/healthz` 200, create → resolve (307) → stats against DynamoDB passed | `scripts/smoke_local.py` against the live URL |
| Lambda canary (run from a workstation with the same CLI the CI job uses) | v2 got 10% via the `live` alias, probe: 39 requests, 0% errors, p95 310 ms, `/version` matched → promoted to 100% in 68 s | `python -m greengate canary` output; alias `live` → v2 |
| Weighted routing through the Function URL | 50% weight on v1 → 16 of 40 requests answered by v1, 24 by v2 | `/version` sampling |
| Flows and agent schema | `flows/greengate-optimizer.yml`, `flows/phoenix-responder.yml`, `agents/greengate-advisor.yml` pass the hackathon `ai-catalog` validator 0.0.10 | `ACTION=validate node index.js` |
| CI YAML | GitLab CI lint API: valid, 0 errors, 0 warnings (root + includes + catalog component) | `scripts/ci_lint_bundle.py` + `POST /projects/:id/ci/lint` |
| Unit tests | 66 passed, app coverage 98% | `pytest -q --cov=app` |

## Pipeline measurements

Pending: GitLab.com requires identity verification of the account before CI jobs run on hosted runners. Until then
no pipeline could be measured, and no numbers are claimed here.

| | Baseline (median of 3) | Optimized (median of 3) | Change |
|---|---|---|---|
| gCO2e per run (app scope) | – | – | – |
| Runner minutes per run | – | – | – |

## Deploy decisions, rollback, MTTR

Pending the same CI runs.
