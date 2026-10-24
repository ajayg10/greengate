# Prompt 2: GreenGate build, phase by phase (for Claude Code)

**How to use this file:**

1. Save **Section A** as `CLAUDE.md` in the repo root, so Claude Code reads it every session.
2. Paste **one phase at a time** from Section B. Start the next phase only after the current one's "Done when" checks pass.
3. Before each phase, start a fresh Claude Code session, or run `/clear`, so context stays clean.

---

## Section A: `CLAUDE.md` (permanent project context)

```markdown
# GreenGate: project context for Claude Code

## What we are building
GreenGate: hands-off, carbon-aware DevSecOps automation on the GitLab Duo Agent Platform, for the GitLab
"Life After Code" Transcend hackathon (Path A, autonomy level: Hands-off). Deadline Oct 27 2026 13:00 UTC.
Prize targets: Best Hands-off Agent + Most Environmentally Impactful + Google Cloud bonus.
Plan: docs/00_GREENGATE_PLAN.md. Infra details: docs/SETUP.md (created by the setup prompt).

## Core loop
push/MR → app pipeline → sci-meter (gCO2e) → if waste or over budget, the bridge triggers the
greengate-optimizer flow → flow opens an MR fixing .gitlab/ci/app.yml → MR pipeline measures before/after and
runs the guardrail → auto-merge if green and improved → main: carbon-aware deploy decision → Cloud Run canary →
SLO probe → promote OR rollback → incident issue → phoenix-responder flow → RCA + fix MR + postmortem → loop.

## Non-negotiable principles
1. Agents decide, pipelines execute. Duo flows only read the repo and write commits, MRs and notes.
   Cloud credentials exist only in CI, through keyless WIF. No JSON keys, ever.
2. Never invent Duo flow/agent YAML fields or tool names. Copy the structure from the hackathon templates in
   this repo and the official flow schema (flow_registry v1 docs) and tools list
   (docs.gitlab.com/user/duo_agent_platform/agents/tools/). If unsure, read the docs or ask me.
3. Never remove or break the hackathon's existing template-validation CI job.
4. Honest numbers only. Every coefficient lives in greengate/sci/coefficients.yaml with a source URL and
   access date. Estimates are labelled as estimates. Projections are labelled as projections.
5. Guardrails: bot MRs may only modify .gitlab/ci/app.yml (and Phoenix fix MRs only app/ + tests/).
   Enforced by a CI policy job, not by trust.
6. Loop safety: commits/MRs by the bot or with label greengate-auto never re-trigger the optimizer.
   At most 1 open optimizer MR at a time. Respect GitLab.com rate limits (backoff + retries on 429).
7. Lean CI: `rules:` everywhere, caching, slim images. We are a carbon project, so our own pipeline must be
   efficient (the "GreenGate machinery" jobs are measured too).

## Repo layout (target)
app/                    FastAPI "EcoLinks" URL shortener (the service we ship)
  main.py, store.py, health.py, Dockerfile, requirements.txt
greengate/              Python package (3.12), CLI entry: `python -m greengate <cmd>`
  config.py             loads greengate.yml (the human "intent" file)
  gitlab_api.py         thin python-gitlab/httpx client with retry/backoff
  sci/meter.py          job durations → kWh → gCO2e (SCI)
  sci/intensity.py      Electricity Maps live → fallback static table
  sci/coefficients.yaml CCF + grid intensity values WITH sources
  ledger.py             append/read JSONL in GCS
  bridge/trigger.py     post @mention / assign to trigger flows
  bridge/automerge.py   verify conditions, set merge-when-pipeline-succeeds
  bridge/policy.py      guardrail: allowed paths for bot MRs
  deploy/decide.py      urgent vs normal, carbon threshold, defer via Cloud Tasks
  deploy/canary.py      deploy revision with no traffic, split 10%, probe, promote/rollback
  deploy/probe.py       synthetic SLO probe (error rate, p95) against the canary tag URL
  report/render.py      markdown reports for MR notes
flows/ agents/          (or whatever paths the hackathon template uses: follow the template)
  greengate-optimizer, phoenix-responder (flows), greengate-advisor (agent)
.gitlab-ci.yml          includes only: template validation + .gitlab/ci/*.yml
.gitlab/ci/app.yml      the APP pipeline: the optimizer's target (starts intentionally unoptimized)
.gitlab/ci/greengate.yml GreenGate machinery jobs (protected by the policy)
dashboard/              FastAPI + a single HTML page (Chart.js via CDN) reading the ledger
infra/                  idempotent gcloud scripts
demo/                   scripted demo scenarios (break.patch, waste.patch, run_demo.md)
docs/                   PLAN, SETUP, ARCHITECTURE, METHODOLOGY, DEMO_SCRIPT, RESULTS
postmortems/            written by phoenix-responder
greengate.yml           intent: carbon budget, SLOs, urgent labels, allowed regions, max defer
tests/                  pytest; unit tests for sci, decide, policy, probe parsing
LICENSE                 MIT

## Tech choices
Python 3.12, FastAPI, httpx, python-gitlab, pydantic, pytest, ruff. Images: python:3.12-slim.
gcloud in CI: google/cloud-sdk:slim (or alpine). Cloud Run for app + dashboard. GCS ledger. Cloud Tasks for deferral.

## Definition of done for every phase
Code + tests pass locally (`pytest -q`, `ruff check`) → pushed → pipeline green on GitLab (check with `glab ci
status`/`glab ci view`) → docs updated → short report to me: what was built, evidence (pipeline URLs, MR
links), what's next, risks. Commit messages: conventional commits (feat:, fix:, ci:, docs:).
```

---

## Section B: phase prompts

### Phase 1: demo app + intentionally unoptimized baseline pipeline + first Cloud Run deploy

```
Read CLAUDE.md and docs/SETUP.md. Execute PHASE 1.

Goal: a real service shipping to Cloud Run through a realistic but WASTEFUL "legacy" pipeline that the
optimizer agent will later improve. The waste must be the kind found in real repos, documented honestly as
"baseline modeled on common CI anti-patterns".

1. app/: FastAPI "EcoLinks" URL shortener.
   - POST /links {url} → {code, short_url}, GET /{code} → 307 redirect, GET /api/links/{code}/stats,
     GET /healthz, GET /version (returns the git SHA and revision).
   - In-memory store behind an interface (Firestore optional later). Input validation, 404s, basic rate limit.
   - pytest tests with >80% coverage of app/. Dockerfile on python:3.12-slim, non-root user, PORT env.
2. .gitlab-ci.yml: keep the hackathon validation job and `include:` .gitlab/ci/app.yml and .gitlab/ci/greengate.yml.
3. .gitlab/ci/app.yml (BASELINE, intentionally unoptimized; add a header comment saying so):
   - full python:3.12 image (not slim), pip install with no cache in every job,
   - stages strictly sequential: lint → test → test-integration → build → deploy; no `needs:`,
   - every job runs on every change (no rules:changes), so docs-only changes rebuild everything,
   - a duplicated test job (unit tests run twice in two jobs),
   - docker build without layer cache or --cache-from.
   It must still be CORRECT: tests pass and the image builds and pushes to Artifact Registry (docker:dind or kaniko).
4. Deploy job (temporary, simple; Phase 5 replaces it): WIF auth → gcloud run deploy ecolinks
   --region $GCP_REGION_PRIMARY --service-account gg-runtime --allow-unauthenticated, only on main.
   Print the URL.
5. greengate.yml (intent file), with comments:
   carbon_budget_g_per_pipeline, sci_regression_tolerance_pct, slo: {max_error_rate, max_p95_ms},
   urgent_labels: [urgent, security, hotfix], allowed_regions, carbon_threshold_g_per_kwh,
   max_defer_minutes (demo mode: 3), canary_steps: [10, 100], probe: {requests, duration_s}.
6. greengate/config.py loads and validates it (pydantic) + tests.

Done when: main pipeline green; the Cloud Run URL works (curl /healthz, create and resolve a link);
README has a "Live demo" link; at least 2 real pipeline runs exist. Report the baseline pipeline duration.
```

### Phase 2: SCI meter + carbon ledger + MR report

```
Read CLAUDE.md. Execute PHASE 2: the SCI meter. This is the scientific core, so accuracy and honesty matter
more than speed.

1. Research (cite each value with its URL in greengate/sci/coefficients.yaml):
   - Cloud Carbon Footprint (CCF) methodology: GCP min/max watts per vCPU, memory kWh per GB-hour, PUE,
     embodied emissions approach.
   - Green Software Foundation SCI spec (ISO/IEC 21031:2024): formula and functional unit.
   - Google Cloud region grid carbon intensity (gCO2eq/kWh) for: the region where GitLab.com hosted runners
     run (find it in GitLab docs), europe-north1, us-central1, asia-south1, us-east1.
   - GitLab.com hosted runner machine sizes (vCPU/RAM per runner tag).
   If you cannot verify a value, mark it `verified: false` and tell me. Never fabricate.
2. greengate/sci/intensity.py: Electricity Maps latest carbon intensity (if ELECTRICITYMAPS_API_KEY is set)
   with a zone mapping, else the static table. Cache for the run. Always return value + source + timestamp.
3. greengate/sci/meter.py:
   - Input: project id + pipeline id. Use the GitLab API to fetch all jobs (pagination!), with started_at,
     finished_at, duration, queued_duration, runner tags, status.
   - Per job: vCPU and RAM from the runner size map; utilization assumption (default 50%, configurable);
     E_job = (vcpu*h*avgW + mem_gb*h*kWh_per_gbh) * PUE; I = intensity(runner region);
     M_job = embodied per vCPU-hour * vcpu * h.
   - Output sci-report.json: per job and total: duration_s, kwh, gco2e_operational, gco2e_embodied,
     gco2e_total, plus assumptions, sources, method version and a hash of coefficients.yaml.
   - Waste detectors (static analysis of .gitlab/ci/app.yml + job data) producing findings with an estimated
     saving each: no_cache, no_rules_changes, sequential_without_needs, duplicate_job, heavy_image,
     docker_no_layer_cache. Each finding has: id, severity, evidence (file:line), estimated_saving_pct.
4. greengate/ledger.py: append one JSON line per pipeline to gs://$GG_LEDGER_BUCKET/ledger/YYYY-MM-DD.jsonl
   (pipeline id, sha, ref, MR iid, total gCO2e, kwh, duration, findings count, kind=baseline/optimized/normal).
5. CLI: `python -m greengate sci --pipeline $CI_PIPELINE_ID --out sci-report.json`.
6. .gitlab/ci/greengate.yml job `sci-meter`: stage .post, `when: always`, needs nothing. It measures every
   OTHER job of the same pipeline (its own and later jobs are excluded; document this), uploads the
   artifact, appends to the ledger, and posts/updates ONE sticky MR note (find it by a hidden HTML marker)
   with a markdown table: total gCO2e, kWh, duration, vs budget, vs main's median of the last 5, top 3
   wasteful jobs, findings. Use report/render.py.
7. Unit tests with fixture job payloads; snapshot test for the markdown.
8. docs/METHODOLOGY.md: formula, every coefficient + source, assumptions (utilization, runner region),
   limitations, and why the % reduction is robust even when the absolute numbers are estimates.

Done when: every pipeline posts an SCI report; ledger lines appear in GCS; METHODOLOGY.md is complete;
run 3 baseline pipelines and report the median gCO2e and duration (save to docs/RESULTS.md).
```

### Phase 3: the greengate-optimizer Duo flow + bridge trigger (highest risk; do it carefully)

```
Read CLAUDE.md and the "Trigger proof" result in docs/SETUP.md. Execute PHASE 3.

1. Study the flow template in this repo and the official flow schema docs + the tools list. Write down in
   docs/ARCHITECTURE.md which exact tools we use and why. Use ONLY tools that exist.
2. Flow "greengate-optimizer" (multi-agent, using the template's exact structure):
   - Agent 1 "auditor": input = the trigger note (it contains the SCI report summary + findings JSON
     embedded in a fenced block). It reads .gitlab/ci/app.yml and greengate.yml via repository tools, then
     produces a prioritized optimization plan. Rules: never change test semantics (same test commands
     must still run), never remove security/test jobs, only touch .gitlab/ci/app.yml.
   - Agent 2 "engineer": applies the plan. Add `cache:` for pip with a key on requirements files,
     `rules: changes:` so docs-only changes skip build/deploy, `needs:` to make lint/test parallel,
     de-duplicate jobs, python:3.12-slim, docker layer caching (--cache-from / kaniko cache).
     Create branch greengate/optimize-<short-sha>, commit, open an MR with labels greengate-auto,
     greengate-optimizer, and a description with: findings addressed, expected saving per finding, and a
     "Guarantees" section (what was NOT changed).
   - Agent 3 "reporter": posts a note on the Control Room issue linking the MR, with the expected saving.
   Prompts must be precise: role, constraints, required output format, a stop condition, and
   "if unsure, make no change and explain".
   Optionally put team rules in AGENTS.md (supported for custom flows) and reference them.
3. Validate with the template's CI validation job, tag the release (e.g. v0.3.0) to publish, then give me
   the exact UI steps to enable the flow with mention + assign triggers. WAIT for my confirmation.
4. greengate/bridge/trigger.py + CI job `gg-bridge` (main branch only, after sci-meter):
   - Conditions: findings exist OR over budget; AND no open MR labelled greengate-optimizer; AND the
     triggering commit is not authored by the bot / not from a greengate-auto MR (loop guard).
   - Action: according to the trigger-proof result, either post a note on the Control Room issue
     "@<optimizer-flow-user> optimize pipeline <id>", with a compact summary + findings JSON, OR assign a
     freshly created issue to the flow user. Keep the note under the size limits.
   - Idempotent: never trigger twice for the same pipeline (marker in note).
5. Test end-to-end: push a normal commit to main → sci-meter → bridge → flow runs → MR appears.
   Iterate on the prompts until the MR is correct and its pipeline is green. Keep a log of the agent session
   links in docs/RESULTS.md.

Done when: on a fresh main pipeline, an optimizer MR is opened WITHOUT any human action, its pipeline is
green, and the MR's sticky SCI note shows the measured before/after.
```

### Phase 4: guardrails + auto-merge (closing the hands-off loop)

```
Read CLAUDE.md. Execute PHASE 4.

1. greengate/bridge/policy.py + MR job `gg-policy` (runs on MRs with label greengate-auto or bot author):
   - Fail if any changed path is outside the allowlist (optimizer: .gitlab/ci/app.yml only;
     phoenix: app/**, tests/**, postmortems/**).
   - Fail if test jobs or test commands were removed or weakened (parse YAML: the set of script lines
     containing pytest must be a superset of main's).
   - Fail if the YAML is invalid (use the GitLab CI lint API).
2. MR job `gg-verdict` (after sci-meter on the MR pipeline): compare the MR pipeline's gCO2e with the median
   of main's last 3 baseline-kind runs. Pass if improvement ≥ sci_regression_tolerance_pct and all jobs
   passed.
3. greengate/bridge/automerge.py, run by gg-verdict when everything passes: approve if needed and set
   auto-merge / merge-when-pipeline-succeeds through the API with the bot token. Post the final verdict note:
   "Auto-merged by GreenGate: −X% gCO2e, −Y% duration, policy OK".
   If anything fails: label needs-human, post why, and do NOT merge (this is the safe failure mode;
   mention it in the README).
4. Tests for policy (allowed, forbidden path, removed pytest) and the verdict math.
5. Run the full loop twice. Record in docs/RESULTS.md: baseline median vs optimized median (3 runs each),
   % reduction in gCO2e and in duration.

Done when: optimizer MR → policy OK → verdict OK → auto-merged with no human, and RESULTS.md has real
numbers. Also show one rejected case (a deliberately bad change) being blocked; keep the MR link for the video.
```

### Phase 5: carbon-aware deploy + Cloud Run canary + automatic rollback

```
Read CLAUDE.md. Execute PHASE 5. Replace the temporary deploy job.

1. greengate/deploy/decide.py → writes deploy.env (dotenv artifact):
   - URGENT if the MR merged into this commit (find it through the API) has any urgent_labels → DEPLOY_NOW.
   - Else get the current intensity for allowed regions. If the primary is below carbon_threshold → DEPLOY_NOW.
     Else if a greener allowed region is below the threshold → DEPLOY_NOW with that region (Tier 2; implement
     if time allows, otherwise just recommend it in the note).
     Else DEFER: create a Cloud Tasks task (scheduleTime = now + defer interval, demo mode 3 min, capped by
     max_defer_minutes) that POSTs to the GitLab pipeline trigger API for this ref with variable
     GG_DEFERRED=true. After max deferral → deploy anyway (never block shipping forever).
   - Log the decision + reasoning + intensity source to the ledger and a note on the commit/MR.
   - GG_FORCE_INTENSITY variable to make the demo deterministic (clearly labelled as demo override).
2. greengate/deploy/canary.py (job `gg-canary`, main only, rules on DEPLOY_NOW or GG_DEFERRED):
   - gcloud run deploy --no-traffic --tag canary-<shortsha> (image by digest).
   - update-traffic: the new revision gets canary_steps[0]% (10%).
   - deploy/probe.py hits the canary tag URL for probe.duration_s: error rate + p95 (plus /version check).
     Optionally also read Cloud Run request metrics from Cloud Monitoring.
   - Pass → promote to 100%, record stable revision = new. Fail → update-traffic back to 100% on the previous
     stable revision (rollback), mark the job failed, write rollback.json (bad revision, stable revision, SHA
     range, probe results, last 50 log lines from Cloud Logging for the bad revision).
   - Store the stable revision name in a GCS object (single source of truth).
3. Measure and print the rollback time (seconds from SLO breach to traffic restored). Put it in RESULTS.md.
4. demo/break.patch: a realistic bug (e.g. a redirect handler that raises on codes with uppercase letters, or
   an accidental O(n²) causing p95 > SLO). Reproduce: apply it on a branch → MR → merge → canary fails →
   rollback.

Done when: a normal merge goes through canary → 100%; break.patch triggers a rollback automatically; a
deferred deploy is re-triggered by Cloud Tasks and then ships. All three are logged in the ledger.
```

### Phase 6: phoenix-responder flow (self-healing)

```
Read CLAUDE.md. Execute PHASE 6.

1. On rollback, the gg-canary job (with the bot token) creates an issue "Incident: canary failed for <sha>",
   labels incident + phoenix, with the body: rollback.json summary, probe numbers, the commit range
   stable..bad, log excerpt, link to the job. Then it triggers phoenix-responder (mention or assign,
   per the trigger-proof result).
2. Flow "phoenix-responder" (same template structure, real tools only):
   - "investigator": reads the incident, the diff of the commit range (repository/commit tools), relevant
     files and tests; produces a root-cause hypothesis with evidence (file:line) and a confidence level.
   - "fixer": if confidence is high, opens a fix MR (app/** + a regression test in tests/** that would have
     caught it), labels greengate-auto phoenix. If confidence is low, opens a revert MR of the bad commit
     instead. Never touches CI or infra files.
   - "scribe": commits postmortems/YYYY-MM-DD-<slug>.md (blameless: summary, impact, timeline from the
     incident data, root cause, fix, carbon note: compute wasted by the bad deploy, action items) into the
     same MR, and comments on the incident with links.
3. The fix MR flows through Phase 4's policy (phoenix allowlist) + tests + verdict (for phoenix MRs the
   verdict = tests green + policy OK; carbon is reported but not required to improve) → auto-merge →
   canary → healthy → the bridge closes the incident with a final note "Resolved in N min, no human
   involved".
4. Run the full scenario end-to-end with demo/break.patch. Save every link in docs/RESULTS.md and record
   the MTTR (mean time to recovery).

Done when: break → rollback → incident → RCA → fix MR with regression test → auto-merge → healthy deploy →
incident closed, with zero human actions.
```

### Phase 7: dashboard + advisor agent

```
Read CLAUDE.md. Execute PHASE 7.

1. dashboard/: FastAPI + one HTML page (Chart.js from a CDN), deployed to Cloud Run "greengate-dashboard"
   via the same WIF pipeline (only when dashboard/** changes). It reads the ledger from GCS (cached 60s).
   Sections:
   - KPI tiles: total gCO2e measured, gCO2e saved vs baseline, % reduction, pipeline minutes saved,
     deploys (now / deferred / rolled back), MTTR.
   - Chart: gCO2e per pipeline over time, with baseline/optimized markers.
   - Table: latest decisions (deploy now / defer / rollback), each with its reason and intensity source.
   - Projection card, clearly labelled "Projection": yearly savings at N pipelines/day (N editable in the UI).
   - "Methodology" link to METHODOLOGY.md. Accessible, readable in light and dark mode, mobile-friendly.
2. Agent "greengate-advisor" (Duo Chat agent, template structure): answers "why was my deploy deferred?",
   "what's my pipeline's carbon footprint?", "what did the last incident cost?" by reading the repo's
   docs/RESULTS.md, greengate.yml, sci reports and postmortems. Publish it with a tag.
3. Add the dashboard URL to the README.

Done when: the dashboard is live with real ledger data, and the advisor agent answers the 3 questions correctly.
```

### Phase 8: final runs, documentation, submission package

```
Read CLAUDE.md. Execute PHASE 8: polish and evidence. Do NOT add features.

1. Clean evidence runs: reset the baseline (revert app.yml to the baseline tag on a branch if needed), run 3
   baseline + 3 optimized pipelines, run the deploy-defer scenario and the break→heal scenario once more.
   Update docs/RESULTS.md with final tables + links.
2. README.md (judges read it first):
   - One-line pitch, 30-second GIF/screenshot, live links (app, dashboard), video link placeholder.
   - Hackathon fit: Path A, autonomy level Hands-off, DevSecOps stages covered (Verify, Package, Secure via
     policy, Release, Deploy, Monitor, Govern via the ledger), and how the Duo Agent Platform is used
     (2 flows + 1 agent, triggers, tools).
   - Architecture diagram (Mermaid) + "Agents decide, pipelines execute" + security model (WIF, least
     privilege, guardrails, loop guards).
   - Results table (gCO2e, duration, rollback time, MTTR), with a link to METHODOLOGY.md.
   - Setup instructions to reproduce, "Limitations & honest notes", MIT license.
3. docs/DEMO_SCRIPT.md: a second-by-second script for a < 3 min video, following the storyline in the plan,
   with the exact commands/patches to run, plus a fallback using recorded clips if live runs are slow.
4. docs/DEVPOST.md: the Devpost submission text (Inspiration, What it does, How we built it, Challenges,
   Accomplishments, What we learned, What's next, Built with), including the Google Cloud usage and the SCI
   numbers.
5. Final checks: repo public, LICENSE detected, flows/agent tagged and published, pipelines visible,
   Cloud Run services live with min-instances 0 (cheap) but warm enough for judges (document it), no secrets
   in history (run gitleaks), all tests green.

Done when: every item on the plan's submission checklist is ticked, with evidence links.
```

---

### Tips for running these phases

- **Phase 3 is the most important.** If triggering the flow from CI doesn't work, fall back to assigning the issue, then to a personal-token mention. Don't spend more than 2 hours stuck on it.
- If a phase runs long, tell Claude Code: *"Reduce scope to the Done-when criteria, skip the optional parts."*
- After every phase, check the pipeline graph yourself in GitLab: judges will look at that same history.
- The Phase 4 rejected MR and the Phase 6 rollback are the strongest moments for the video. Screen-record them while they happen (OBS), so you don't depend on live runs on the final day.
