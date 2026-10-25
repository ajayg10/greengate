"""Print Markdown tables for docs/RESULTS.md straight from the S3 ledger (no hand-typed numbers).

    GG_LEDGER_BUCKET=... python scripts/results_table.py
"""

from __future__ import annotations

import os
import statistics

from greengate.dashboard_data import app_failures
from greengate.ledger import Ledger

PIPE = "https://gitlab.com/ajayg10/greengate/-/pipelines/"


def link(pid) -> str:
    return f"[#{pid}]({PIPE}{pid})"


def main() -> None:
    rows = Ledger(os.environ["GG_LEDGER_BUCKET"]).records()
    pipes = [r for r in rows if r.get("kind") in {"baseline", "optimized", "normal"}]
    print(
        "| Pipeline | Profile | Complete | App gCO2e | App runner min | Total gCO2e | "
        "Same minutes on GitLab hosted (counterfactual) |"
    )
    print("|---|---|---|---|---|---|---|")
    for r in pipes:
        hosted = r.get("gco2e_if_gitlab_hosted")
        print(f"| {link(r['pipeline_id'])} | {r['kind']}{' (deferred re-run)' if r.get('deferred') else ''} | "
              f"{'no: ' + ', '.join(app_failures(r)) if app_failures(r) else 'yes'} | {r['app_gco2e']:.4f} | "
              f"{r['app_runner_minutes']:.2f} | {r['gco2e_total']:.4f} | {f'{hosted:.4f}' if hosted else 'n/a'} |")
    for kind in ("baseline", "optimized"):
        ok = [r for r in pipes if r["kind"] == kind and not app_failures(r) and not r.get("deferred")]
        if ok:
            g = statistics.median(r["app_gco2e"] for r in ok)
            m = statistics.median(r["app_runner_minutes"] for r in ok)
            print(
                f"\n**{kind} median** over {len(ok)} complete runs: "
                f"{g:.4f} gCO2e (app scope), {m:.2f} runner minutes"
            )
    print("\n| Pipeline | Event | Detail |")
    print("|---|---|---|")
    for r in rows:
        k = r.get("kind")
        if k == "decision":
            print(
                f"| {link(r['pipeline_id'])} | decision: {r['action']} | "
                f"{r['reason']} (source: {r['intensity']['source']}) |"
            )
        elif k == "deploy":
            p = r.get("probe") or {}
            print(
                f"| {link(r['pipeline_id'])} | promoted v{r['new_version']} (from v{r['stable_version']}) | "
                f"probe {p.get('requests')} requests, {p.get('error_rate', 0):.0%} errors, p95 {p.get('p95_ms')} ms |"
            )
        elif k == "rollback":
            p = r.get("probe") or {}
            print(f"| {link(r['pipeline_id'])} | rolled back v{r['bad_version']} to v{r['stable_version']} | "
                  f"probe p95 {p.get('p95_ms')} ms, {p.get('error_rate', 0):.0%} errors; traffic restored "
                  f"{r.get('rollback_seconds')} s after the breach was detected |")


if __name__ == "__main__":
    main()
