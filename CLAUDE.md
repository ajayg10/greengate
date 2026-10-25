# GreenGate: project context for Claude Code

## What we are building
GreenGate: hands-off, carbon-aware DevSecOps automation on the GitLab Duo Agent Platform, for the GitLab
"Life After Code" Transcend hackathon (Path A, autonomy level: Hands-off). Deadline Oct 27 2026 13:00 UTC.
Prize targets: Best Hands-off Agent + Most Environmentally Impactful. Cloud: **AWS** (Lambda + Amplify), not
Google Cloud (see docs/SETUP.md for why and for the Cloud Run/GCS/Cloud Tasks/WIF -> AWS mapping).
Plan: docs/00_GREENGATE_PLAN.md. Infra: docs/SETUP.md. Method: docs/METHODOLOGY.md. Design: docs/ARCHITECTURE.md.

## Core loop
push/MR -> app pipeline (.gitlab/ci/app.yml) -> gg-sci-meter (gCO2e) -> gg-bridge @mentions the
greengate-optimizer flow -> flow opens an MR fixing .gitlab/ci/app.yml -> MR pipeline measures before/after,
gg-policy guardrail, gg-verdict -> auto-merge with the bot token -> main: gg-deploy-decide (urgent / grid
intensity / defer via EventBridge Scheduler + gg-deferrer Lambda) -> gg-canary (Lambda alias 10% + SLO probe)
-> promote OR rollback -> incident issue -> phoenix-responder flow -> fix MR + regression test + postmortem.

## Non-negotiable principles
1. Agents decide, pipelines execute. Duo flows only read the repo and write commits, MRs and notes.
   AWS credentials exist only in CI through keyless OIDC (gg-ci any ref, gg-deployer main only). No keys, ever.
2. Never invent Duo flow/agent YAML fields or tool names. Follow flows/*.yml, agents/*.yml, the hackathon
   participant template and the ai-catalog component schemas (`validate-items` job must pass).
3. Never remove or break the hackathon's template-validation CI job.
4. Honest numbers only. Coefficients live in greengate/sci/coefficients.yaml with source URL + access date.
   Estimates are labelled estimates, projections are labelled projections. docs/RESULTS.md has only real runs.
5. Guardrails: optimizer MRs may only modify .gitlab/ci/app.yml; phoenix MRs only app/, tests/, postmortems/.
6. Loop safety: greengate-auto commits/MRs never re-trigger the optimizer; max 1 open optimizer MR.
7. Lean CI for our own jobs: rules everywhere, pip cache, python:3.12-slim.

## Dev commands
- `python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt -r requirements-greengate.txt jsonschema`
- `pytest -q` and `ruff check .`
- AWS infra: `bash infra/aws/bootstrap.sh`, `IMAGE_URI=... bash infra/aws/app.sh` (idempotent)
- GitLab setup: `powershell -File infra/gitlab/setup.ps1`, pipelines: `powershell -File infra/gitlab/ci.ps1 status`
- Commit messages: conventional commits (feat:, fix:, ci:, docs:).
