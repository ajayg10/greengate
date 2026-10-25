# GreenGate: infrastructure setup

No secret values appear in this file. Everything here was created by the idempotent scripts in `infra/`.

## 1. Decision: AWS instead of Google Cloud (2026-10-25)

The Google Cloud account could not be set up, so GreenGate deploys to AWS (Lambda + Amplify). The hackathon FAQ
says "Deploy anywhere"; the cost is that the Google Cloud bonus (up to +0.2) is not claimed. The plan and build
prompts were written for Google Cloud; read them through this mapping:

| Plan (Google Cloud) | GreenGate on AWS |
|---|---|
| Cloud Run service | AWS Lambda from a container image (Lambda Web Adapter) + Function URL |
| Cloud Run canary (tagged revision, 10%) | Lambda versions + weighted `live` alias (verified: 50% weight → 16/40 requests on the other version) |
| Canary tag URL | `canary` alias with its own Function URL |
| Artifact Registry | Amazon ECR `greengate` |
| GCS ledger | Amazon S3 `greengate-ledger-221082195174` |
| Cloud Tasks | EventBridge Scheduler one-time schedule → `gg-deferrer` Lambda → GitLab pipeline trigger API |
| Workload Identity Federation | IAM OIDC provider `gitlab.com` + `sts:AssumeRoleWithWebIdentity` |
| Firestore (optional) | DynamoDB `ecolinks-links` (on-demand) |
| Cloud Run dashboard | AWS Amplify Hosting (static, deployed by CI) |
| europe-north1 / us-central1 | eu-north-1 (Stockholm) / us-west-2 (variable only) |

## 2. AWS resources (account 221082195174, region eu-north-1)

Created by `infra/aws/bootstrap.sh` and `infra/aws/app.sh`.

| Resource | Name | Notes |
|---|---|---|
| S3 bucket | `greengate-ledger-221082195174` | private, versioned; `ledger/` records, `state/` stable version |
| ECR repository | `greengate` | scan on push, keeps 20 images, Lambda pull policy |
| DynamoDB table | `ecolinks-links` | key `code`, pay per request |
| Lambda | `ecolinks` | container image, x86_64, 512 MB; aliases `live` and `canary` |
| Function URL (live) | https://u72w7ug22esgggvbemt4ovrfnu0xrfpe.lambda-url.eu-north-1.on.aws/ | the app |
| Function URL (canary) | https://ze77ok3l5fg2vfmm2c4krqcmhu0gmddv.lambda-url.eu-north-1.on.aws/ | what the SLO probe hits |
| Lambda | `gg-deferrer` | python3.12, arm64 (Graviton), reads SSM `/greengate/gitlab-trigger-token` |
| Scheduler group | `gg-deferred-deploys` | one-time deferral schedules (auto-deleted after running) |
| Amplify app | `greengate-dashboard` (`d28t6c9al9r6ob`), branch `main` | https://main.d28t6c9al9r6ob.amplifyapp.com/ |
| IAM OIDC provider | `gitlab.com` (audience `https://gitlab.com`) | |
| IAM role `gg-deployer` | trusted by `project_path:ajayg10/greengate:ref_type:branch:ref:main` only | deploy rights (Lambda, scheduler, Amplify, ledger) |
| IAM role `gg-ci` | trusted by `project_path:ajayg10/greengate:*` | ledger read/write + image push only |
| IAM roles `gg-runtime`, `gg-deferrer`, `gg-scheduler` | Lambda/Scheduler execution roles | least privilege, see `infra/aws/policies/` |
| Budget | `greengate-monthly` ($10) | email alerts at 50%/100% actual and 100% forecast |

## 3. GitLab project

| Item | Value |
|---|---|
| Project | https://gitlab.com/ajayg10/greengate (id 88050627), public, created 2026-10-24 |
| Default branch | `main`, protected (Maintainers push/merge, no force push) |
| MR settings | pipelines must succeed, delete source branch, pipeline variables: Maintainers only |
| Labels | greengate-auto, greengate-optimizer, incident, phoenix, urgent, security, hotfix, carbon-deferred, sci-report, needs-human |
| Control Room issue | #1 |
| Setup script | `infra/gitlab/setup.ps1` (idempotent) |

**Hackathon project:** the hackathon group, subgroup and project are created by GitLab after the contributor
onboarding is approved. Account `ajayg10` was not a member of `gitlab-ai-hackathon` on 2026-10-25, so GreenGate lives
in a personal public project for now. To move: push the repo to the hackathon project, re-run
`infra/gitlab/setup.ps1 -ProjectPath <path>` and `infra/aws/bootstrap.sh` with both paths in
`GITLAB_PROJECT_PATHS`, then re-run pipelines there (pipeline history does not move with `git push`).

### CI/CD variables

| Variable | Protected | Set by |
|---|---|---|
| AWS_ACCOUNT_ID, AWS_REGION_PRIMARY, AWS_REGION_SECONDARY | no | setup.ps1 |
| AWS_DEPLOYER_ROLE_ARN | yes | setup.ps1 |
| AWS_CI_ROLE_ARN, ECR_REPOSITORY_URI, GG_LEDGER_BUCKET, GG_SCHEDULE_GROUP | no | setup.ps1 |
| GG_RUNTIME_ROLE_ARN, GG_SCHEDULER_ROLE_ARN, GG_DEFERRER_ROLE_ARN, GG_DEFERRER_ARN | no | setup.ps1 |
| GG_FUNCTION_NAME, GG_APP_URL, GG_AMPLIFY_APP_ID, GG_DASHBOARD_URL | no | setup.ps1 |
| GG_CONTROL_ISSUE_IID, GG_DEMO_MODE | no | setup.ps1 |
| GITLAB_BOT_TOKEN | masked + protected | **human**, in the UI |
| GG_OPTIMIZER_FLOW_USER, GG_PHOENIX_FLOW_USER | no | **human**, after enabling the flows |
| CATALOG_SYNC_TOKEN | masked + protected | **human** (hackathon project only) |
| ELECTRICITYMAPS_API_KEY | masked | optional |

## 4. Facts used by the SCI meter

GitLab.com hosted Linux runners run on Google Cloud (x86-64 in us-east1/us-east5, Arm64 in us-central1);
`saas-linux-small-amd64` (default) = 2 vCPU / 8 GB. Source:
https://docs.gitlab.com/ci/runners/hosted_runners/linux/ (accessed 2026-10-24). All coefficients:
`greengate/sci/coefficients.yaml`, method: `docs/METHODOLOGY.md`.

## 5. Human TODO (UI-only or identity steps)

1. **Identity verification on GitLab.com.** New free accounts must verify (phone or card) before CI jobs run:
   pipelines failed with "Identity verification is required in order to run CI jobs".
2. **Hackathon onboarding** for the GitLab account that should own the submission (Duo Agent Platform access,
   "Developer + AI" role, 500 credits).
3. **Default GitLab Duo namespace:** gitlab.com → avatar → Preferences → Default GitLab Duo namespace → the hackathon group.
4. **Bot token:** avatar → Preferences → Access tokens → Add new token: name `greengate-bot`, scope `api`, expiry
   2026-11-30. Then project → Settings → CI/CD → Variables → Add variable `GITLAB_BOT_TOKEN`, Masked + Protected.
5. **Trigger token for deferred deploys:** run `powershell -File infra/gitlab/trigger-token.ps1` (creates the
   GitLab trigger and stores it in SSM; never printed).
6. **Enable the flows** (hackathon project): tag a release (`git tag v0.4.0 && git push origin v0.4.0`) so
   `catalog-sync` publishes `flows/` and `agents/`, or create them in AI → Flows → New flow by pasting the
   `definition`. Enable each flow in the project with the Mention trigger, then set `GG_OPTIMIZER_FLOW_USER` and
   `GG_PHOENIX_FLOW_USER` to the service-account usernames GitLab shows.
7. Record the demo video (docs/DEMO_SCRIPT.md) and upload it to YouTube (public or unlisted).

## 6. Smoke test

`gg-smoke` (manual on main): assumes `gg-deployer` and `gg-ci` through OIDC, lists Lambda functions, the ECR repo
and the ledger bucket, writes `smoke/<pipeline>.txt` with `gg-ci`, and posts "GreenGate smoke test OK" on the
Control Room issue when `GITLAB_BOT_TOKEN` is set. Run it with `glab ci run -b main --variables GG_RUN_SMOKE:true`.
