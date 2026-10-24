# GreenGate: master plan

> **Update 2026-10-25:** the cloud is now **AWS**, not Google Cloud (the Google Cloud account could not be set up).
> Read every Cloud Run / GCS / Cloud Tasks / WIF mention through the mapping in `docs/SETUP.md`.
> The Google Cloud bonus (+0.2) is no longer targeted.

**Hackathon:** Life After Code, GitLab Transcend (Devpost)
**Deadline:** Tue Oct 27, 13:00 UTC = **18:30 IST**. Internal deadline: **Tue 12:00 IST**.
**Path:** A (Start Fresh)
**Autonomy level:** Hands-off
**Prizes targeted:** Best Hands-off Agent ($5k) + Most Environmentally Impactful ($5k), plus the Google Cloud bonus (+0.2)

---

## 1. One-line pitch

> **GreenGate is a hands-off, carbon-aware path to production.** You set the intent: *"ship safely, stay under the carbon budget."* GitLab Duo agents measure each pipeline's carbon footprint (ISO/IEC 21031 SCI), rewrite wasteful CI themselves, ship through a Cloud Run canary at a low-carbon time, and when production breaks, roll back, find the cause and open the fix. No human is in the loop.

## 2. The closed loop (what the demo shows)

```
 Developer pushes code / opens MR
          │
          ▼
 ┌─────────────────────────────── GitLab CI pipeline ───────────────────────────────┐
 │ lint/test/build (app pipeline)  ──►  sci-meter (measures gCO2e for this pipeline) │
 └──────────────────────────────────────────────┬────────────────────────────────────┘
                                                │ over budget / waste detected
                                                ▼
                       CI "bridge" posts note:  @ai-greengate-optimizer …
                                                │
                                                ▼
 ┌──────────── Duo Flow: greengate-optimizer (3 agents) ────────────┐
 │ Auditor → finds waste (cache, rules:changes, needs:, image size)  │
 │ Engineer → rewrites .gitlab/ci/app.yml, opens MR [greengate-auto] │
 │ Reporter → explains the expected saving on the MR                 │
 └──────────────────────────────┬───────────────────────────────────┘
                                ▼
 MR pipeline: tests + sci-meter (measured before/after) + guardrail policy check
                                │ all green AND SCI improved
                                ▼
                       auto-merge (hands-off)
                                │
                                ▼
 main pipeline: carbon-aware deploy decision
   urgent (hotfix/security) → deploy now
   normal → deploy if grid intensity < threshold, else defer (Cloud Tasks re-triggers the pipeline later)
                                │
                                ▼
 Cloud Run canary: new revision at 10% → SLO probe (error rate, p95)
        pass → promote 100%           fail → instant rollback to the stable revision
                                                │
                                                ▼
                         CI opens incident issue + @ai-phoenix-responder
                                                │
                                                ▼
 ┌──────────── Duo Flow: phoenix-responder (3 agents) ──────────────┐
 │ Investigator → reads incident, the diff stable..bad, the job logs │
 │ Fixer → opens fix (or revert) MR                                  │
 │ Scribe → writes blameless postmortem to /postmortems              │
 └──────────────────────────────────────────────────────────────────┘
                                │ fix MR goes through the same loop
                                ▼
 Carbon ledger (GCS) → GreenGate dashboard (Cloud Run): CO2e per run, savings, deploy decisions
```

**Architecture principle:** agents decide, pipelines execute. Duo agents only write commits, MRs and notes. Cloud credentials live only in CI (keyless Workload Identity Federation). Say this in the video.

## 3. Components

| Component | What it is | Where it runs |
|---|---|---|
| `app/` "EcoLinks" | Small FastAPI URL-shortener API: the service being shipped | Cloud Run |
| `greengate/sci` | SCI meter: job durations → kWh → gCO2e | CI job |
| `greengate/bridge` | Posts @mentions and assigns issues to trigger flows; auto-merge | CI job |
| `greengate/deploy` | Carbon-aware decision, canary, SLO probe, rollback | CI job |
| `flows/greengate-optimizer` | Duo multi-agent flow | GitLab Duo Agent Platform |
| `flows/phoenix-responder` | Duo multi-agent flow | GitLab Duo Agent Platform |
| `agents/greengate-advisor` | Duo chat agent: "why was my deploy deferred?" | Duo Chat |
| `dashboard/` | Emissions and decisions dashboard | Cloud Run |
| Ledger | JSONL in a GCS bucket | GCP |

## 4. SCI method (summary; full version goes in `docs/METHODOLOGY.md`)

`SCI = ((E × I) + M) / R`

- **E (kWh)** = Σ jobs [vCPU × hours × avg W per vCPU + mem GB × hours × kWh per GB-hour] × PUE. Uses Cloud Carbon Footprint (CCF) coefficients for GCP: min 0.71 W and max 4.26 W per vCPU, 0.000392 kWh per GB-hour of memory, PUE 1.1. **Verify these values and cite the source.**
- **I (gCO2e/kWh)** = grid intensity of the runner's region. Use Electricity Maps live data if an API key is set; otherwise the static per-region values Google publishes for Cloud regions (cited in the coefficients file).
- **M** = embodied emissions per vCPU-hour, amortized. The value is configurable and its source is cited.
- **R** = one pipeline run. Also report per successful deploy.
- Take the **median of 3 runs** for baseline and optimized, so the comparison is fair.

## 5. Timeline (IST)

| When | Phase | Output |
|---|---|---|
| **Sat (today)** | Human: Devpost + contributor registration, Discord, GCP billing | Accounts ready |
| Sat evening | **Setup prompt** (GitLab + GCP) | Repo, CI variables, WIF, Cloud Run reachable |
| Sat night | Build P1: app + baseline pipeline + first deploy | Green pipeline, live URL |
| Sun morning | Build P2: SCI meter + ledger | gCO2e posted on MRs |
| **Sun afternoon** | Build P3: optimizer flow + bridge trigger (**riskiest; prove it early**) | Agent opens an MR on its own |
| Sun night | Build P4: guardrail + auto-merge | Hands-off loop closed |
| Mon morning | Build P5: carbon-aware deploy + canary + rollback | Live canary on Cloud Run |
| Mon afternoon | Build P6: Phoenix flow + postmortem | Self-healing demo works |
| Mon evening | Build P7: dashboard + advisor agent | Visuals for the video |
| Mon night | Build P8: 3× baseline/optimized runs, docs, README | Real numbers |
| **Tue morning** | Record video, Devpost write-up, submit by **12:00 IST** | Submitted |

**Cut line if time is short:** drop P7's advisor agent first, then the dashboard (a static chart in the README is enough). Never cut P3, P5 or the measured numbers.

## 6. Who does what

| Human only (you) | Claude Code |
|---|---|
| Devpost registration, GitLab contributor application | All code, CI YAML, flow/agent YAML |
| Set **Default GitLab Duo namespace** (Preferences UI) | glab/API setup: variables, labels, tokens via API where allowed |
| Create the Project Access Token if the API is blocked | GCP setup via gcloud: APIs, Artifact Registry, WIF, service accounts, bucket, Cloud Tasks |
| Enable flows + triggers in the GitLab UI (if no API) | Tests, docs, demo scripts |
| GCP billing account, Electricity Maps API key (free) | |
| Record the video, submit on Devpost | |

## 7. Top risks and mitigations

1. **A flow doesn't fire when the bot token @mentions it.** Test in P3 before anything else. Fallback A: assign the issue to the flow's service user via the API. Fallback B: post the mention with your personal token. Fallback C (last resort): the human posts the mention in the video.
2. **Contributor approval is late.** Build P1–P2 in a personal GitLab project. When approved, push to the hackathon project and **re-run pipelines there**, because pipeline history does not move with `git push`.
3. **Unclear flow YAML schema.** Claude Code must copy the template in the hackathon project and the official flow schema docs, and must not invent fields or tool names. The template's CI validation job must stay.
4. **Tiny absolute numbers.** Lead with % reduction, plus a clearly labelled projection at team scale.
5. **GitLab.com rate limits (from Oct 19) and CI minutes.** Keep pipelines lean, use `rules:` and avoid loops. Bot-authored commits must not re-trigger the optimizer.

## 8. Video storyline (< 3 min)

1. 0:00–0:20 Problem: "CI/CD burns compute nobody measures, and releases still need humans watching."
2. 0:20–0:50 Intent: the `greengate.yml` policy (carbon budget, SLOs, urgent labels). Push a feature.
3. 0:50–1:30 SCI meter flags waste → optimizer agent opens an MR on its own → measured −X% → auto-merged.
4. 1:30–2:10 Carbon-aware deploy → canary → promote. Then a buggy MR → canary fails → rollback in seconds → Phoenix opens RCA + fix MR + postmortem.
5. 2:10–2:45 Dashboard: total gCO2e saved, decisions, methodology (SCI, CCF).
6. 2:45–3:00 Architecture slide: "Agents decide, pipelines execute." Closing line.

## 9. Submission checklist

- [ ] Public GitLab repo, `LICENSE` (MIT) visible on the repo page
- [ ] Pipeline history shows many real runs (optimizer MRs, deploys, a rollback)
- [ ] At least one custom public **flow** (two in this plan) plus an agent, tagged and published
- [ ] Deployed on Google Cloud (Cloud Run) and live until judging ends
- [ ] README: architecture, setup, autonomy level = Hands-off, path = A, the numbers
- [ ] `docs/METHODOLOGY.md` with SCI formula, coefficients and sources
- [ ] YouTube video < 3 min, public, no copyrighted music
- [ ] Devpost text: features, how it uses the Duo Agent Platform, impact numbers, Google Cloud usage
