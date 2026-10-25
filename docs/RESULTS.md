# GreenGate results

Only measured values from real runs, each linked to its pipeline. Generated from the S3 ledger by
`scripts/results_table.py` (no hand-typed numbers). SCI grams are estimates by method (docs/METHODOLOGY.md);
percentages compare the same jobs with the same method.

**Where CI runs:** GreenGate's own GitLab runner on AWS EC2 t3.large in eu-north-1 (Stockholm), see
docs/METHODOLOGY.md §3b. The last column prices the same job minutes on GitLab's default hosted runner
(Google Cloud us-east1) as a **counterfactual estimate**, not a measurement.

## Pipeline measurements (state on 2026-10-25)

| Pipeline | Profile | Complete | App gCO2e | App runner min | Total gCO2e | Same minutes on GitLab hosted (counterfactual) |
|---|---|---|---|---|---|---|
| [#2985956508](https://gitlab.com/ajayg10/greengate/-/pipelines/2985956508) | baseline | no: build-image | 0.0507 | 2.43 | 0.0507 | n/a |
| [#2985977395](https://gitlab.com/ajayg10/greengate/-/pipelines/2985977395) | baseline | yes | 0.0369 | 1.77 | 0.0673 | 0.3096 |
| [#2985988058](https://gitlab.com/ajayg10/greengate/-/pipelines/2985988058) | baseline | yes | 0.0378 | 1.81 | 0.0687 | 0.3158 |
| [#2985998994](https://gitlab.com/ajayg10/greengate/-/pipelines/2985998994) | baseline | yes | 0.0428 | 2.06 | 0.0714 | 0.3286 |
| [#2986006432](https://gitlab.com/ajayg10/greengate/-/pipelines/2986006432) | baseline | yes | 0.0377 | 1.81 | 0.0689 | 0.3169 |
| [#2986014308](https://gitlab.com/ajayg10/greengate/-/pipelines/2986014308) | baseline | yes | 0.0405 | 1.94 | 0.0532 | 0.2447 |
| [#2986021392](https://gitlab.com/ajayg10/greengate/-/pipelines/2986021392) | normal (deferred re-run) | yes | 0.0000 | 0.00 | 0.0290 | 0.1332 |

**baseline median** over 5 complete runs: 0.0378 gCO2e (app scope), 1.81 runner minutes

| Pipeline | Event | Detail |
|---|---|---|
| [#2985977395](https://gitlab.com/ajayg10/greengate/-/pipelines/2985977395) | decision: DEPLOY_NOW | eu-north-1 grid at 35 gCO2e/kWh, below 150 (source: static: Ember 2025 Sweden national average as cited by https://inzonex.co.uk/poweratlas/sweden/ (Ember: 99% low-carbon in 2025) (accessed 2026-10-25)) |
| [#2985977395](https://gitlab.com/ajayg10/greengate/-/pipelines/2985977395) | promoted v3 (from v2) | probe 39 requests, 0% errors, p95 45.9 ms |
| [#2985988058](https://gitlab.com/ajayg10/greengate/-/pipelines/2985988058) | decision: DEPLOY_NOW | eu-north-1 grid at 35 gCO2e/kWh, below 150 (source: static: Ember 2025 Sweden national average as cited by https://inzonex.co.uk/poweratlas/sweden/ (Ember: 99% low-carbon in 2025) (accessed 2026-10-25)) |
| [#2985988058](https://gitlab.com/ajayg10/greengate/-/pipelines/2985988058) | promoted v4 (from v3) | probe 39 requests, 0% errors, p95 36.2 ms |
| [#2985998994](https://gitlab.com/ajayg10/greengate/-/pipelines/2985998994) | decision: DEPLOY_NOW | eu-north-1 grid at 35 gCO2e/kWh, below 150 (source: static: Ember 2025 Sweden national average as cited by https://inzonex.co.uk/poweratlas/sweden/ (Ember: 99% low-carbon in 2025) (accessed 2026-10-25)) |
| [#2985998994](https://gitlab.com/ajayg10/greengate/-/pipelines/2985998994) | rolled back v5 to v4 | probe p95 1841.0 ms, 0% errors; traffic restored 0.03 s after the breach was detected |
| [#2986006432](https://gitlab.com/ajayg10/greengate/-/pipelines/2986006432) | decision: DEPLOY_NOW | eu-north-1 grid at 35 gCO2e/kWh, below 150 (source: static: Ember 2025 Sweden national average as cited by https://inzonex.co.uk/poweratlas/sweden/ (Ember: 99% low-carbon in 2025) (accessed 2026-10-25)) |
| [#2986006432](https://gitlab.com/ajayg10/greengate/-/pipelines/2986006432) | promoted v6 (from v4) | probe 39 requests, 0% errors, p95 38.2 ms |
| [#2986014308](https://gitlab.com/ajayg10/greengate/-/pipelines/2986014308) | decision: DEFER | eu-north-1 grid at 420 gCO2e/kWh is above 150: re-checking in 3 min (source: demo override (GG_FORCE_INTENSITY), not a measurement) |
| [#2986021392](https://gitlab.com/ajayg10/greengate/-/pipelines/2986021392) | decision: DEPLOY_NOW | eu-north-1 grid at 35 gCO2e/kWh, below 150 (source: static: Ember 2025 Sweden national average as cited by https://inzonex.co.uk/poweratlas/sweden/ (Ember: 99% low-carbon in 2025) (accessed 2026-10-25)) |
| [#2986021392](https://gitlab.com/ajayg10/greengate/-/pipelines/2986021392) | promoted v7 (from v6) | probe 39 requests, 0% errors, p95 47.8 ms |


## What the events show

- **Carbon-aware deferral, end to end, no human:** pipeline #2986014308 ran with the demo override
  `GG_FORCE_INTENSITY=420` (labelled "demo override, not a measurement" in the log, the commit note and the ledger).
  The decision was DEFER; an EventBridge Scheduler one-time schedule invoked the `gg-deferrer` Lambda 3 minutes later,
  which re-triggered #2986021392 with `GG_DEFERRED=true`. That pipeline skipped the whole app pipeline (only the deploy
  jobs ran), re-checked the real grid (35 gCO2e/kWh), and shipped v7 through the canary.
- **A real automatic rollback:** in #2985998994 the new Lambda version answered every probe request correctly but its
  p95 was 1841 ms (SLO 1500 ms). GreenGate rolled traffic back to v4 automatically. The cause was container-image cold
  starts, not a code bug, so the probe now warms the canary until answers are consistently fast before measuring
  (commit 0d05eb3); the next three canaries passed with p95 38-48 ms.
- **Low-carbon runner:** the same job minutes would emit about 4.6x more on GitLab's hosted runners in us-east1
  (counterfactual column), because the Stockholm grid is far cleaner.

## Still to measure

- Optimized runs (after the greengate-optimizer Duo flow's MR merges) and the baseline-vs-optimized reduction.
- The break → rollback → incident → phoenix-responder fix → healthy deploy scenario and its MTTR.

Both need the GitLab Duo Agent Platform (hackathon group access) and the bot token.
