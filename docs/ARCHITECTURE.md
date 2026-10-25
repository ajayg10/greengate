# GreenGate architecture

**Agents decide, pipelines execute.** GitLab Duo flows read the repository and write commits, merge requests and
notes. GitLab CI pipelines hold the cloud credentials (keyless OIDC to AWS) and do every measurement, merge and
deploy. Guardrails are CI jobs, not trust.

```mermaid
flowchart TD
  dev[Push or MR] --> app[App pipeline<br/>lint · tests · image build<br/>.gitlab/ci/app.yml]
  app --> sci[gg-sci-meter<br/>SCI gCO2e per run<br/>ledger in S3]
  sci -->|waste or over budget, main only| bridge[gg-bridge<br/>@mention on Control Room issue]
  bridge --> opt[Duo flow greengate-optimizer<br/>auditor → engineer → reporter]
  opt -->|MR labelled greengate-auto| mr[MR pipeline<br/>tests + SCI before/after]
  mr --> pol[gg-policy<br/>allowlist · tests not weakened]
  pol --> ver[gg-verdict<br/>carbon cut ≥ tolerance?]
  ver -->|yes| merge[auto-merge with bot token]
  ver -->|no| human[label needs-human, stop]
  merge --> dec[gg-deploy-decide<br/>urgent? grid below threshold?]
  dec -->|dirty grid| defer[EventBridge Scheduler<br/>→ gg-deferrer Lambda<br/>→ re-trigger pipeline later]
  defer --> dec
  dec -->|deploy now| can[gg-canary<br/>Lambda version at 10% via alias weights<br/>SLO probe on canary URL]
  can -->|SLO ok| prom[promote to 100%]
  can -->|SLO breach| rb[instant rollback<br/>incident issue + @mention]
  rb --> phx[Duo flow phoenix-responder<br/>investigator → fixer → scribe]
  phx -->|fix MR + regression test + postmortem| mr
  sci --> dash[gg-dashboard<br/>AWS Amplify static dashboard]
```

## Components

| Piece | Where it runs | What it does |
|---|---|---|
| `app/` EcoLinks | AWS Lambda (container image, Lambda Web Adapter), DynamoDB | The service being shipped |
| `.gitlab/ci/app.yml` | GitLab CI | App pipeline; starts intentionally wasteful; owned by the optimizer flow |
| `.gitlab/ci/greengate.yml` | GitLab CI | GreenGate machinery jobs (protected by the policy) |
| `greengate/sci` | CI job `gg-sci-meter` | SCI meter + waste detectors |
| `greengate/bridge` | CI jobs `gg-bridge`, `gg-policy`, `gg-verdict` | Trigger flows, guardrail, verdict, auto-merge, incidents |
| `greengate/deploy` | CI jobs `gg-deploy-decide`, `gg-canary`; Lambda `gg-deferrer` | Carbon-aware decision, deferral, canary, rollback |
| `flows/greengate-optimizer.yml` | GitLab Duo Agent Platform | 3 agents: auditor, engineer, reporter |
| `flows/phoenix-responder.yml` | GitLab Duo Agent Platform | 3 agents: investigator, fixer, scribe |
| `agents/greengate-advisor.yml` | GitLab Duo Chat | Answers "why was my deploy deferred?" and friends |
| `dashboard/` | AWS Amplify Hosting | Static dashboard rebuilt from the ledger by CI |
| Ledger | Amazon S3 (`ledger/YYYY-MM-DD/<pipeline>-<kind>.json`) | One immutable record per measurement or decision |

## Duo Agent Platform usage

Flows use only tools from the AI Catalog's tool list (`schemas/component/tools.json` of the
[ai-catalog component](https://gitlab.com/components/ai-catalog)), and both flows and the agent pass that
component's schema validation (`validate-items` job).

| Flow / agent | Tools | Why |
|---|---|---|
| optimizer · auditor | `get_repository_file`, `list_repository_tree`, `get_issue`, `list_issue_notes` | read app.yml, intent file, rules, the trigger note |
| optimizer · engineer | `get_repository_file`, `ci_linter`, `create_commit`, `create_merge_request`, `gitlab_merge_request_search` | write the new YAML, lint it, commit to a new branch (`start_branch`), open the MR, avoid duplicates |
| optimizer · reporter | `create_issue_note`, `get_merge_request` | report the MR and expected saving |
| phoenix · investigator | `get_issue`, `list_issue_notes`, `list_commits`, `get_commit`, `get_commit_diff`, `get_repository_file`, `list_repository_tree`, `get_job_logs` | evidence: incident body, diff of stable..bad, logs |
| phoenix · fixer | `get_repository_file`, `get_commit_diff`, `create_commit`, `create_merge_request`, `gitlab_merge_request_search` | fix + regression test, or revert |
| phoenix · scribe | `get_issue`, `get_merge_request`, `create_commit`, `create_issue_note` | postmortem into the same MR, comment on the incident |
| advisor (chat) | `get_repository_file`, `list_repository_tree`, `get_job_logs`, `get_issue`, `list_issues`, `list_issue_notes`, `get_merge_request`, `gitlab_api_get` | read results, intent, logs, postmortems |

**Triggers:** CI posts `@<flow service account> …` on the Control Room issue (mention trigger) for the optimizer, and
on the incident issue for phoenix. `GG_TRIGGER_MODE=assign` switches to assigning a fresh issue (assign trigger).
GitLab blocks AI workflows from merging, so the merge is done by the `gg-verdict` job with the bot token, after the
policy and verdict passed.

## Security model

- **No cloud keys in CI.** Jobs request a GitLab OIDC token (`id_tokens`, `aud: https://gitlab.com`) and call
  `sts:AssumeRoleWithWebIdentity`. Role trust policies accept only `project_path:<this project>`:
  `gg-deployer` only for `ref:main`, `gg-ci` for any ref but with ledger and image-push rights only.
- **Least privilege:** the deployer can touch only the `ecolinks`, `greengate-dashboard` and `gg-deferrer`
  functions, the `greengate` ECR repo, the ledger bucket, schedules in `gg-deferred-deploys` and Amplify apps.
  Policies are in `infra/aws/policies/`.
- **Secrets:** `GITLAB_BOT_TOKEN` is a masked, protected CI variable. The pipeline trigger token used by
  `gg-deferrer` lives in SSM Parameter Store as a SecureString and is never logged.
- **Guardrails:** `gg-policy` fails bot MRs that touch files outside their allowlist or remove or weaken tests.
  Bot MRs are recognised by label *or* branch prefix, so a missing label cannot skip the check.
- **Loop safety:** commits from `greengate-auto` MRs never re-trigger the optimizer; at most one optimizer MR is
  open; each pipeline triggers at most once (HTML marker); API calls back off on 429/5xx.
- **Safe failure mode:** if anything fails, GreenGate labels the MR `needs-human`, explains why and does not merge.

## Why AWS Lambda + Amplify

The original plan targeted Google Cloud Run. The Google Cloud account could not be set up, so GreenGate runs on
AWS (the hackathon allows any cloud). Lambda scales to zero and has native weighted aliases, which give a real
10% canary and a rollback that is one API call. Amplify hosts the static dashboard. Idle cost is close to zero.
