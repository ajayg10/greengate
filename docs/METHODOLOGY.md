# GreenGate SCI methodology

GreenGate measures the **Software Carbon Intensity (SCI)** of every GitLab CI pipeline run, following the
Green Software Foundation SCI specification (ISO/IEC 21031:2024).

```
SCI = ((E × I) + M) per R          R = one CI pipeline run
```

Everything below is implemented in [`greengate/sci/meter.py`](../greengate/sci/meter.py), and every number comes
from [`greengate/sci/coefficients.yaml`](../greengate/sci/coefficients.yaml), which lists a source URL, an access
date and a `verified` flag for each value. Each SCI report records the SHA-256 of that file, so a result can always
be traced to the exact coefficients that produced it.

## 1. Inputs: real job data from GitLab

The `gg-sci-meter` job runs in the `.post` stage of every pipeline. It reads all jobs of its own pipeline from the
GitLab jobs API (with pagination): `duration`, `queued_duration`, `status`, `tag_list`, runner description.

- Measured: jobs with status `success`, `failed` or `canceled` and a recorded duration.
- Excluded: the meter itself and the other `.post` jobs (they are still running), manual and skipped jobs.
- Queue time is reported but not counted (no runner VM is busy for the job while it waits).

## 2. Energy E (kWh)

For each job, with `h` = job duration in hours:

```
avgW per vCPU = minW + utilization × (maxW − minW)
E_job = (vCPU × h × avgW / 1000  +  memory_GB × h × kWh_per_GBh) × PUE
```

| Coefficient | Value | Source |
|---|---|---|
| Min watts per vCPU (Google Cloud) | 0.71 W | [Cloud Carbon Footprint methodology](https://www.cloudcarbonfootprint.org/docs/methodology) |
| Max watts per vCPU (Google Cloud) | 4.26 W | same |
| CPU utilization | 50% (CCF's fallback when utilization is unknown) | same |
| Memory | 0.000392 kWh per GB-hour | same |
| PUE (Google Cloud) | 1.1 | same |

vCPU and memory come from the runner tag. GitLab.com hosted Linux runners
([docs](https://docs.gitlab.com/ci/runners/hosted_runners/linux/)): `saas-linux-small-amd64` (the default for
untagged jobs) has 2 vCPU and 8 GB; `medium` 4/16; `large` 8/32, and so on. The tag is read from the job's
`tag_list`, then from the runner description, and falls back to the default.

## 3. Grid intensity I (gCO2e/kWh)

GitLab.com hosted runners run on Google Cloud Compute Engine: x86-64 runners in `us-east1` and `us-east5`,
Arm64 runners in `us-central1` (same GitLab docs page). GreenGate uses **us-east1 = 579 gCO2eq/kWh**, the 2025 grid
carbon intensity Google publishes for that region
([Carbon free energy for Google Cloud regions](https://cloud.google.com/sustainability/region-carbon)). us-east5 is
lower (343), so using us-east1 is the conservative choice.

When `ELECTRICITYMAPS_API_KEY` is set, the meter and the deploy decision use Electricity Maps live data for the
zone mapped in `coefficients.yaml` and record "Electricity Maps live, zone …" as the source. Without a key, or
when the API fails, they use the static table. Every report states which one was used.

## 4. Embodied emissions M

CCF/SCI allocation ([CCF embodied emissions](https://www.cloudcarbonfootprint.org/docs/embodied-emissions)):

```
M = TE × (TR / EL) × (RR / TR_total)
```

- TE = 2321.46 kgCO2e for the Google Cloud `n2d` family (EPYC 2nd Gen), from the
  [CCF coefficients dataset](https://raw.githubusercontent.com/cloud-carbon-footprint/ccf-coefficients/main/output/coefficients-gcp-embodied.csv)
- EL = 4 years (CCF default), RR / TR_total = job vCPUs / 224 (the largest n2d machine)
- Result: **0.2958 gCO2e per vCPU-hour**, so `M_job = 0.2958 × vCPU × h`

**Not verified:** GitLab does not document which machine family backs its runners. `n2d` is an assumption
(`mapping_verified: false` in the coefficients file). Embodied carbon is reported separately in every SCI report,
so a reader can drop it.

## 5. Worked example

One minute on `saas-linux-small-amd64`:

- avgW = 0.71 + 0.5 × (4.26 − 0.71) = 2.485 W per vCPU
- E = (2 × 1/60 × 2.485 / 1000 + 8 × 1/60 × 0.000392) × 1.1 = 0.000149 kWh (0.149 Wh)
- E × I = 0.000149 × 579 = 0.0862 g
- M = 0.2958 × 2 × 1/60 = 0.0099 g
- **SCI ≈ 0.096 gCO2e per runner minute** on the default runner

## 6. Comparing runs fairly

- **Like-for-like scope:** the verdict compares the *app scope* (the app's lint, test and build jobs). Deploy jobs
  and GreenGate's own machinery jobs are excluded from this comparison because they differ between merge request and
  `main` pipelines. Both totals are stored in the ledger.
- **Medians:** baseline and optimized numbers are medians of several runs (`docs/RESULTS.md` says how many).
- **Why the % reduction is robust when the grams are estimates:** every term (energy, intensity, embodied) is a
  product of runner-time and constant coefficients. For the same runner size and region, the ratio
  optimized / baseline is just the ratio of vCPU-seconds, so the percentage holds even if the true wattage, PUE
  or grid intensity is different. Grams are labelled as estimates everywhere.

## 7. Waste detectors and their saving estimates

Static analysis of `.gitlab/ci/app.yml` (with `file:line` evidence) combined with measured job carbon:

| Finding | Estimated saving of affected jobs | Note |
|---|---|---|
| `no_cache` | 30% | pip downloads and wheel builds repeated in every job |
| `heavy_image` | 15% | python:3.12 (~1 GB) instead of python:3.12-slim |
| `duplicate_job` | 100% | an identical job removed (tests still run once) |
| `docker_no_layer_cache` | 40% | build without `--cache-from` |
| `no_rules_changes` | 0% on code changes | up to 100% of the pipeline on docs-only changes |
| `sequential_without_needs` | 0% | saves wall-clock time, **not energy**; reported honestly as zero |

These factors are estimates that only rank the findings. The optimizer merge request's own pipeline measures the
real effect.

## 8. Limitations

- Job wall time × published coefficients is a model, not a wattmeter reading.
- Hosted runners are shared VMs. The model assumes the job owns the whole VM for its duration, which is how GitLab
  bills minutes.
- Docker-in-Docker services run on the same VM, so they are inside the job's runtime and not added again.
- Network transfer and storage (artifacts, caches, the container registry) are not included.
- Lambda runtime energy is not part of the pipeline SCI. It is small next to CI here, and AWS does not publish
  per-request energy.
- AWS publishes no per-region grid intensity. The `eu-north-1` (Stockholm) value of 35 gCO2e/kWh is a Swedish
  national average cited via a secondary source (`verified: false`). With an Electricity Maps key, zone `SE-SE3`
  is used live instead.
