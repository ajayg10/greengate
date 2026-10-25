# GreenGate

**A hands-off, carbon-aware path to production on the GitLab Duo Agent Platform.**
You write one intent, *"ship safely, stay under the carbon budget"*. GitLab Duo agents measure every pipeline's
Software Carbon Intensity (ISO/IEC 21031 SCI), rewrite wasteful CI themselves, ship through a Lambda canary when
the grid is clean, and when production breaks they roll back, find the cause and open the fix.

Built for the GitLab **"Life After Code" Transcend** hackathon · **Path A** (start fresh) · autonomy level **Hands-off**.

| | Link |
|---|---|
| Live app (EcoLinks API on AWS Lambda) | https://u72w7ug22esgggvbemt4ovrfnu0xrfpe.lambda-url.eu-north-1.on.aws/ ([/docs](https://u72w7ug22esgggvbemt4ovrfnu0xrfpe.lambda-url.eu-north-1.on.aws/docs)) |
| Carbon dashboard (AWS Amplify) | https://main.d28t6c9al9r6ob.amplifyapp.com/ |
| Pipelines (public history) | https://gitlab.com/ajayg10/greengate/-/pipelines |
| Control Room issue | https://gitlab.com/ajayg10/greengate/-/issues/1 |
| Demo video | *added on Devpost* |

## What it does

1. **Measures.** Every pipeline ends with `gg-sci-meter`: job runtimes from the GitLab API → kWh (Cloud Carbon
   Footprint coefficients, PUE) × the grid intensity of the Google Cloud region where GitLab.com runners run, plus
   embodied hardware carbon → gCO2e per run. It posts a sticky SCI note on MRs and appends to a ledger in S3.
2. **Finds waste.** Detectors read `.gitlab/ci/app.yml` and the measured jobs: no pip cache, heavy images,
   duplicate jobs, missing `rules:changes`, no `needs:`, Docker builds without layer cache. Each finding has
   `file:line` evidence and an estimated saving.
3. **Fixes it, hands-off.** On `main`, `gg-bridge` @mentions the **greengate-optimizer** Duo flow on the Control Room
   issue. Its three agents (auditor → engineer → reporter) rewrite only `.gitlab/ci/app.yml` and open an MR.
4. **Verifies and merges.** The MR pipeline measures the before/after. `gg-policy` rejects any bot change outside
   its allowlist or any removed/weakened test. `gg-verdict` requires a measured carbon cut ≥ 10%, then auto-merges
   with the bot token. Otherwise it labels `needs-human` and stops (the safe failure mode).
5. **Deploys when the grid is clean.** `gg-deploy-decide` ships urgent MRs now (labels `urgent`, `security`,
   `hotfix`). Otherwise it deploys if the region's grid is below 150 gCO2e/kWh, or defers: an EventBridge Scheduler
   one-time schedule invokes the `gg-deferrer` Lambda, which re-triggers the pipeline later. Max deferral: 4 h.
6. **Canary, rollback, self-heal.** `gg-canary` publishes a new Lambda version, sends it 10% of live traffic with
   weighted alias routing, and probes the canary URL (error rate, p95, `/version`). Pass → 100%. Fail → instant
   rollback, an incident issue, and an @mention of the **phoenix-responder** flow (investigator → fixer → scribe),
   which opens a fix MR with a regression test and a blameless postmortem. That MR goes through the same loop.

**Agents decide, pipelines execute.** Duo agents only read the repo and write commits, MRs and notes. AWS
credentials exist only in CI, via keyless OIDC.

## Architecture

```mermaid
flowchart LR
  A[push / MR] --> B[app pipeline] --> C[gg-sci-meter<br/>SCI gCO2e]
  C -->|waste| D[@greengate-optimizer<br/>Duo flow] --> E[bot MR]
  E --> F[gg-policy + gg-verdict] -->|greener, green| G[auto-merge]
  G --> H{gg-deploy-decide}
  H -->|dirty grid| I[EventBridge Scheduler<br/>→ gg-deferrer] --> H
  H -->|deploy| J[Lambda canary 10%<br/>SLO probe]
  J -->|ok| K[100%]
  J -->|breach| L[rollback + incident] --> M[@phoenix-responder<br/>Duo flow] --> E
  C --> N[S3 ledger] --> O[Amplify dashboard]
```

Details, tools per agent and the security model: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How GitLab Duo Agent Platform is used

- **2 custom flows** in `flows/` (`greengate-optimizer`, `phoenix-responder`), 3 agents each, multi-agent routing,
  passing results with `context:<agent>.final_answer`. Only catalog tools are used (validated by the hackathon's
  `ai-catalog` component, `validate-items` job).
- **1 custom agent** in `agents/` (`greengate-advisor`) for Duo Chat: "why was my deploy deferred?", "what is my
  pipeline's carbon footprint?", "what did the last incident cost?".
- **Triggers:** mention (CI posts the @mention), with an assign-trigger fallback (`GG_TRIGGER_MODE=assign`).
- **Rules for agents:** [AGENTS.md](AGENTS.md).

## DevSecOps stages covered

Verify (tests, SCI meter) · Package (image to ECR) · Secure (guardrail policy, least-privilege OIDC roles) ·
Release (bot MRs, auto-merge) · Deploy (carbon-aware, canary) · Monitor (SLO probe, logs) · Govern (carbon ledger,
budget in `greengate.yml`).

## Results

Measured numbers only, with links to the pipelines: **[docs/RESULTS.md](docs/RESULTS.md)**.
Method, every coefficient and its source: **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)**.

## Run it yourself

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt -r requirements-greengate.txt
pytest -q && ruff check .
# AWS (idempotent): S3 ledger, ECR, IAM roles, GitLab OIDC provider, scheduler, budget
GG_BUDGET_EMAIL=you@example.com GITLAB_PROJECT_PATHS="<group>/<project>" bash infra/aws/bootstrap.sh
# build + push the image, then create DynamoDB, Lambda + aliases + Function URLs, gg-deferrer, Amplify
IMAGE_URI=<account>.dkr.ecr.eu-north-1.amazonaws.com/greengate:<sha> bash infra/aws/app.sh
# GitLab project settings, labels, Control Room issue, CI variables (Windows PowerShell + glab)
powershell -File infra/gitlab/setup.ps1
```

Then add `GITLAB_BOT_TOKEN` (masked, protected), enable the two flows and the agent in the project, and set
`GG_OPTIMIZER_FLOW_USER` / `GG_PHOENIX_FLOW_USER` to their service-account usernames. Full setup: [docs/SETUP.md](docs/SETUP.md).

## Limitations and honest notes

- SCI grams are **estimates** (job time × published coefficients), not wattmeter readings. The % reduction compares
  the same jobs with the same method, so it is the robust number.
- Runner machine family (`n2d`) for embodied carbon is an assumption; embodied is reported separately.
- The deploy region's grid value without an Electricity Maps key is a national average (`verified: false`).
- `GG_FORCE_INTENSITY` exists only to make the deferral demo deterministic; it is labelled "demo override" in
  every log and note, and never affects SCI measurements.
- Runs on AWS instead of Google Cloud: the Google Cloud account could not be set up in time, so the Google Cloud
  bonus is not claimed.

## License

[MIT](LICENSE)
