# Devpost submission text (draft)

Fields marked ⟨…⟩ are filled from docs/RESULTS.md once the pipeline runs are measured. Nothing is claimed before it
is measured.

**Project name:** GreenGate

**Tagline:** Duo agents that measure, cut and schedule your CI/CD carbon, and heal production on their own.

## Inspiration
CI/CD runs all day, and almost nobody knows what it costs the planet. When we do look, the waste is boring and
fixable: no caches, 1 GB images, the same tests twice, full rebuilds for a README typo. Releases still need a human
watching dashboards at deploy time. We wanted the path after "git push" to look after itself, and to be honest
about carbon while it does.

## What it does
You write one intent file, `greengate.yml`: a carbon budget per pipeline, SLOs, urgent labels, and the grid intensity
above which deploys should wait. Then GreenGate runs hands-off:
- **Measures** every pipeline with the Software Carbon Intensity spec (ISO/IEC 21031): job runtimes from the GitLab
  API, Cloud Carbon Footprint power coefficients, PUE, and Google's published grid intensity for us-east1, where
  GitLab.com runners run, plus embodied hardware carbon. Every coefficient has a source URL and access date.
- **Finds waste** in the pipeline file with `file:line` evidence, and **triggers the greengate-optimizer Duo flow**,
  whose auditor, engineer and reporter agents rewrite only `.gitlab/ci/app.yml` and open a merge request.
- **Verifies and merges:** the MR pipeline measures before/after; a CI policy job rejects any bot change outside its
  allowlist or any removed test; a verdict job auto-merges only on a measured cut ≥ 10%.
- **Deploys when the grid is clean:** urgent fixes ship now; otherwise it deploys below 150 gCO2e/kWh or defers with
  an EventBridge Scheduler re-trigger.
- **Canary and self-healing:** a new AWS Lambda version gets 10% of live traffic through weighted aliases; an SLO
  probe decides promote or instant rollback. On rollback, the **phoenix-responder** Duo flow investigates the
  incident and the commit diff, opens a fix MR with a regression test, and writes a blameless postmortem.
- A **dashboard** on AWS Amplify shows every measured run and every deploy decision with its reason and data source.

## How we built it
- **GitLab Duo Agent Platform:** 2 custom multi-agent flows (3 agents each, results passed with
  `context:<agent>.final_answer`) and 1 Duo Chat agent, using only AI Catalog tools (`get_repository_file`,
  `ci_linter`, `create_commit`, `create_merge_request`, `get_commit_diff`, `get_job_logs`, `create_issue_note`, …).
  Validated with the hackathon's `ai-catalog` component. Triggered by @mentions that CI posts.
- **GitLab CI/CD:** the app pipeline the agents optimize, plus lean machinery jobs (`gg-sci-meter`, `gg-bridge`,
  `gg-policy`, `gg-verdict`, `gg-deploy-decide`, `gg-canary`, `gg-dashboard`). "Agents decide, pipelines execute."
- **AWS:** Lambda (container image with the Lambda Web Adapter) + Function URLs + weighted aliases, DynamoDB,
  ECR, S3 (carbon ledger), EventBridge Scheduler + a Graviton Lambda for deferrals, Amplify Hosting, keyless
  GitLab OIDC → IAM roles (no access keys anywhere), least-privilege policies, a $10 budget alarm.
- **Python 3.12, FastAPI, httpx, pydantic, pytest** (66 tests, app coverage 98%).

## Challenges we ran into
- Making flows safe enough to act alone: we put the guardrails in CI (allowlist, "tests may only be de-duplicated,
  never removed"), recognised bot MRs by label *or* branch so a missing label can't skip them, and made the failure
  mode "label needs-human and stop".
- Honest carbon numbers: per-run grams are tiny and model-based, so we lead with the percentage, keep embodied
  carbon separate, flag unverified values, and never let the demo override touch a measurement.
- Our Google Cloud account could not be activated in time, so we moved to AWS and rebuilt the canary on Lambda
  weighted aliases (and verified the Function URL honours the weights).

## Accomplishments that we're proud of
- A closed loop from measurement to merged fix to carbon-aware deploy to self-healing rollback.
- Verified on live AWS: a canary at 10% with 39 probe requests, 0% errors, p95 310 ms, promoted to 100% in 68 s.
- ⟨Measured: −X% gCO2e and −Y% runner minutes per pipeline, median of 3 baseline vs 3 optimized runs.⟩
- ⟨Rollback in N s from SLO breach to traffic restored; MTTR M min with zero human actions.⟩

## What we learned
Agents are great at the judgement part (what to change, why it broke) and pipelines are great at the trust part
(credentials, measurements, merges). Splitting them that way made the whole system safer and easier to explain.

## What's next
Live grid data for the deploy region by default, carbon budgets per team, and running the optimizer across a whole
group's pipelines.

## Built with
gitlab-duo-agent-platform, gitlab-ci, python, fastapi, aws-lambda, aws-amplify, dynamodb, amazon-s3, amazon-ecr,
eventbridge-scheduler, openid-connect, chart.js

## Links
- GitLab repository: https://gitlab.com/ajayg10/greengate
- Live app: https://u72w7ug22esgggvbemt4ovrfnu0xrfpe.lambda-url.eu-north-1.on.aws/
- Dashboard: https://main.d28t6c9al9r6ob.amplifyapp.com/
- Video: ⟨YouTube link⟩

## Prize opt-ins
- Most Environmentally Impactful: measured with SCI (ISO/IEC 21031), method and sources in docs/METHODOLOGY.md.
- Google Cloud bonus: **not claimed** (deployed on AWS).
