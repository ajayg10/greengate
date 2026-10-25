# GreenGate demo video script (under 3 minutes)

Record in 1080p. Keep two browser tabs ready: the GitLab project and the GreenGate dashboard.
Narrate in your own voice (or a clear text-to-speech voice). No copyrighted music.
Fallback: if a live run is slow, cut to the recorded clip of the same step (record each step once beforehand).

| Time | Screen | Say |
|---|---|---|
| 0:00–0:15 | Title card: "GreenGate: ship safely, stay under the carbon budget" | "CI/CD burns compute nobody measures, and releases still need someone watching them. GreenGate hands that whole path to GitLab Duo agents." |
| 0:15–0:35 | `greengate.yml` in the repo | "A human writes one intent file: a carbon budget per pipeline, SLOs, which labels are urgent, and the grid intensity above which deploys wait. Everything after this is hands-off." |
| 0:35–0:55 | A main pipeline graph, then the `gg-sci-meter` log and the sticky SCI note on an MR | "Every pipeline measures its own Software Carbon Intensity, the ISO SCI standard: job time, Cloud Carbon Footprint power coefficients, and the grid of the Google Cloud region where GitLab runners run. This baseline pipeline is over budget, and the meter found six kinds of waste, each with a file and line." |
| 0:55–1:25 | Control Room issue: the bridge's @mention → the Duo session → the optimizer MR | "The pipeline mentions our greengate-optimizer flow. Three Duo agents, an auditor, an engineer and a reporter, rewrite only the app pipeline file and open a merge request with the expected saving and a list of guarantees." |
| 1:25–1:45 | MR pipeline: `gg-policy` green, `gg-verdict` log, "Auto-merged by GreenGate: −X% gCO2e" note | "The MR pipeline measures before and after. A policy job checks the bot only touched its one file and kept every test. The verdict sees a measured cut of X percent, so GreenGate merges. No human." |
| 1:45–2:00 | `gg-deploy-decide` log + commit note: deploy now or deferred | "On main, GreenGate decides when to deploy. Urgent fixes ship now. Otherwise it checks the grid: if it's dirty, an EventBridge schedule re-runs the deploy later." |
| 2:00–2:30 | Apply `demo/break.patch` → merge → `gg-canary` log: 10% → SLO breach → rollback in N s → incident issue → phoenix MR + postmortem | "This change looks fine and passes unit tests, but it uses a DynamoDB reserved word. The Lambda canary gets 10% of traffic, the probe sees errors, and GreenGate rolls back in N seconds. The phoenix-responder flow reads the incident and the logs, opens a fix with a regression test, and writes a blameless postmortem." |
| 2:30–2:45 | Dashboard | "The dashboard shows every measured run, the cut per pipeline, and each deploy decision with its reason and data source. Projections are labelled as projections." |
| 2:45–3:00 | Architecture slide (docs/ARCHITECTURE.md diagram) | "Agents decide, pipelines execute. Keyless OIDC, least privilege, guardrails in CI. GreenGate: the hands-off, carbon-aware path to production." |

## Exact commands

```bash
# 1. baseline runs (push any small code change, or re-run main)
glab ci run -b main
# 2. break the app (on a branch, then merge the MR)
git checkout -b demo/break && git apply demo/break.patch && git commit -am "feat: record last visit on each redirect" && git push -u origin demo/break
# 3. force a dirty grid to show a deferral (demo override, clearly labelled in the logs)
glab ci run -b main --variables GG_FORCE_INTENSITY:420
```
