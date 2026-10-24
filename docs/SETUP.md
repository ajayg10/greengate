# GreenGate: infrastructure setup

Status: **in progress** (AWS part done, GitLab part waiting on login and hackathon onboarding).
No secret values appear in this file.

## 1. Decision: AWS instead of Google Cloud (2026-10-25)

The Google Cloud account could not be set up, so GreenGate deploys to AWS.

- Allowed: the hackathon FAQ says "Deploy anywhere". Path A does not even require a deployment.
- Cost of this choice: the Google Cloud bonus (up to +0.2, max score 5.2) is **no longer targeted**.
- The plan and build prompts still say Cloud Run / GCS / Cloud Tasks / WIF. Read them through this mapping:

| Plan (Google Cloud) | GreenGate on AWS | Why |
|---|---|---|
| Cloud Run service | AWS Lambda (container image) + Function URL | Scales to zero, generous free tier |
| Cloud Run canary (tagged revision, 10% traffic) | Lambda versions + weighted alias (`live` alias, 10% to new version) | Native traffic split, rollback = one alias update |
| Canary tag URL for the probe | `canary` alias with its own Function URL | Probe hits only the new version |
| Artifact Registry | ECR repository `greengate` | Same-region image pull for Lambda |
| GCS ledger bucket | S3 bucket `greengate-ledger-<account>` | Private, versioned |
| Cloud Tasks deferred deploy | EventBridge Scheduler one-time schedule → `gg-deferrer` Lambda → GitLab pipeline trigger API | Scheduler can't call arbitrary HTTP, so a tiny Lambda does the POST |
| Workload Identity Federation | IAM OIDC provider `gitlab.com` + `AssumeRoleWithWebIdentity` | Keyless, no access keys in CI |
| Service account gg-deployer | IAM role `gg-deployer` (main branch only) | |
| (new) | IAM role `gg-ci` (any branch: ledger + image push only) | MR pipelines (also bot MRs) must write SCI data but must never deploy |
| Service account gg-runtime | IAM role `gg-runtime` (Lambda execution role) | |
| Cloud Monitoring / Logging | CloudWatch metrics / CloudWatch Logs | |
| Budget alert | AWS Budget `greengate-monthly` | |
| Region europe-north1 | `eu-north-1` (Stockholm) | Low-carbon grid |
| Region us-central1 | `us-west-2` (Oregon), variable only for now | Fallback region; no resources yet |

To verify during the build (not yet tested, do not rely on it blindly):
- A Function URL attached to an alias honours the alias's weighted routing (needed for the 10% canary).
- Public Function URLs (`AuthType NONE`) need both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`
  in the resource policy on newer accounts.
- FastAPI runs on Lambda through the AWS Lambda Web Adapter (the app keeps listening on `$PORT`).

## 2. AWS resources (created 2026-10-25 by `infra/aws/bootstrap.sh`)

Account `221082195174`, primary region `eu-north-1`. Re-running the script is safe.

| Resource | Name / ARN | Notes |
|---|---|---|
| S3 bucket | `greengate-ledger-221082195174` | Public access blocked, versioning on, old versions expire after 30 days |
| ECR repository | `221082195174.dkr.ecr.eu-north-1.amazonaws.com/greengate` | Scan on push, keeps 20 newest images, Lambda pull policy |
| IAM role | `arn:aws:iam::221082195174:role/gg-runtime` | Lambda execution role: CloudWatch Logs + read `ledger/*` |
| IAM role | `arn:aws:iam::221082195174:role/gg-deferrer` | Lambda execution role: CloudWatch Logs + read SSM `/greengate/gitlab-trigger-token` |
| IAM role | `arn:aws:iam::221082195174:role/gg-scheduler` | EventBridge Scheduler may invoke `gg-deferrer` only |
| Scheduler group | `gg-deferred-deploys` | Holds one-time "deploy later" schedules |
| IAM OIDC provider | `arn:aws:iam::221082195174:oidc-provider/gitlab.com` | Audience `https://gitlab.com` |
| IAM role | `gg-deployer` | **Pending**: needs the GitLab project path |
| IAM role | `gg-ci` | **Pending**: needs the GitLab project path |
| Budget | `greengate-monthly` ($10/month) | Email alerts at 50% and 100% actual, 100% forecast (account-wide) |
| SSM parameter | `/greengate/gitlab-trigger-token` (SecureString) | **Pending**: you store it yourself (see Human TODO) |

### Security model
- CI never holds AWS keys. Each job requests a GitLab `id_tokens` JWT (`aud: https://gitlab.com`) and exchanges it
  with `sts:AssumeRoleWithWebIdentity`.
- The trust policy only accepts tokens whose `sub` is `project_path:<our project>:ref_type:branch:ref:main`
  (`gg-deployer`) or `project_path:<our project>:*` (`gg-ci`). Other GitLab projects cannot assume the roles.
- `gg-deployer` can only touch the Lambda functions `ecolinks`, `greengate-dashboard`, `gg-deferrer`, the
  `greengate` ECR repo, the ledger bucket, and schedules in `gg-deferred-deploys`. `iam:PassRole` is limited to
  `gg-runtime`/`gg-deferrer` (for Lambda) and `gg-scheduler` (for Scheduler).
- Policy templates live in `infra/aws/policies/`, so judges can review them.

## 3. Facts for the SCI meter (looked up 2026-10-24)

Source: [GitLab docs, hosted runners on Linux](https://docs.gitlab.com/ci/runners/hosted_runners/linux/).

- GitLab.com hosted Linux runners run on **Google Cloud Compute Engine**: x86-64 in `us-east1` and `us-east5`,
  Arm64 in `us-central1`. Each job gets its own short-lived VM.
- Untagged jobs run on `saas-linux-small-amd64`.

| Runner tag | vCPU | RAM | Free tier |
|---|---|---|---|
| `saas-linux-small-amd64` (default) | 2 | 8 GB | yes |
| `saas-linux-medium-amd64` | 4 | 16 GB | yes |
| `saas-linux-small-arm64` | 2 | 8 GB | yes |
| `saas-linux-large-amd64` | 8 | 32 GB | Premium/Ultimate |
| `saas-linux-xlarge-amd64` | 16 | 64 GB | Premium/Ultimate |
| `saas-linux-2xlarge-amd64` | 32 | 128 GB | Premium/Ultimate |
| `saas-linux-medium-arm64` | 4 | 16 GB | Premium/Ultimate |
| `saas-linux-large-arm64` | 8 | 32 GB | Premium/Ultimate |

So the runner region (where CI energy is used) stays Google Cloud `us-east1`, even though the app runs on AWS.

## 4. Hackathon facts (from the Devpost resources page, read 2026-10-25)

- The hackathon group, subgroup, project and onboarding issue are **created automatically by GitLab after the
  contributor onboarding request is approved** (about 24 business hours). They can't be created by us.
- Participants get the "Developer + AI" role and 500 GitLab Credits per month for Duo Agent Platform features.
- MIT license required, `LICENSE` at the repo root. Repo must be public with visible pipeline history.
- Project should stay live until winners are announced (around Nov 16).

## 5. GitLab

**Pending**: waiting for `glab auth login` and the hackathon project.

## 6. Planned CI/CD variables

| Variable | Value | Masked/protected |
|---|---|---|
| `AWS_ACCOUNT_ID` | `221082195174` | no |
| `AWS_REGION_PRIMARY` | `eu-north-1` | no |
| `AWS_REGION_SECONDARY` | `us-west-2` | no |
| `AWS_DEPLOYER_ROLE_ARN` | `arn:aws:iam::221082195174:role/gg-deployer` | protected |
| `AWS_CI_ROLE_ARN` | `arn:aws:iam::221082195174:role/gg-ci` | no |
| `ECR_REPOSITORY_URI` | `221082195174.dkr.ecr.eu-north-1.amazonaws.com/greengate` | no |
| `GG_LEDGER_BUCKET` | `greengate-ledger-221082195174` | no |
| `GG_SCHEDULE_GROUP` | `gg-deferred-deploys` | no |
| `GG_RUNTIME_ROLE_ARN` | `arn:aws:iam::221082195174:role/gg-runtime` | no |
| `GG_SCHEDULER_ROLE_ARN` | `arn:aws:iam::221082195174:role/gg-scheduler` | no |
| `GG_CONTROL_ISSUE_IID` | pending | no |
| `GG_DEMO_MODE` | `true` | no |
| `GITLAB_BOT_TOKEN` | you set it in the UI | masked + protected + hidden |
| `ELECTRICITYMAPS_API_KEY` | optional, you set it in the UI | masked + protected |
