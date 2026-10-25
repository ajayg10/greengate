"""Markdown for the sticky SCI note on merge requests and for Control Room messages."""

from __future__ import annotations

from typing import Any

STICKY_MARKER = "<!-- greengate:sci-report -->"


def fmt_g(g: float) -> str:
    return f"{g:.3f} g" if g < 10 else f"{g:.1f} g"


def fmt_pct(new: float, old: float | None) -> str:
    if not old:
        return "n/a"
    delta = 100.0 * (new - old) / old
    return f"{delta:+.1f}%"


def sci_note(report: dict[str, Any], *, budget_g: float, main_median_g: float | None,
             findings: list[dict[str, Any]], pipeline_url: str = "", scope: str = "total") -> str:
    t = report[scope] if scope in report else report["total"]
    over = t["gco2e_total"] > budget_g
    lines = [
        STICKY_MARKER,
        "### GreenGate SCI report",
        "",
        f"Software Carbon Intensity of this pipeline run (ISO/IEC 21031 SCI, R = one pipeline run). "
        f"Method `{report['method_version']}`, coefficients `{(report.get('coefficients_sha256') or '')[:12]}`.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Carbon (estimate) | **{fmt_g(t['gco2e_total'])} CO2e** "
        f"(operational {fmt_g(t['gco2e_operational'])}, embodied {fmt_g(t['gco2e_embodied'])}) |",
        f"| Energy | {t['kwh'] * 1000:.2f} Wh |",
        f"| Runner time | {t['runner_minutes']:.2f} min over {t['jobs']} jobs |",
        f"| Budget | {fmt_g(budget_g)} per pipeline: {'**over budget**' if over else 'within budget'} |",
        f"| vs. main (median of last 5) | {fmt_pct(t['gco2e_total'], main_median_g)} |",
        "",
    ]
    top = report["jobs"][:3]
    if top:
        lines += ["**Top jobs by carbon**", "", "| Job | Runner | Time | gCO2e |", "|---|---|---|---|"]
        lines += [f"| `{j['name']}` | {j['runner_tag']} | {j['duration_s']:.0f} s | {j['gco2e_total']:.3f} |"
                  for j in top]
        lines.append("")
    if findings:
        lines += ["**Waste findings**", "", "| Finding | Jobs | Est. saving | Evidence |", "|---|---|---|---|"]
        for f in findings:
            saving = f"{f['estimated_saving_pct']:.1f}%"
            if f.get("conditional_saving"):
                saving += f" ({f['conditional_saving']})"
            lines.append(f"| `{f['id']}` | {', '.join(f['jobs'][:4])} | {saving} | {', '.join(f['evidence'][:2])} |")
        lines.append("")
    region_notes = [f"{r}: {i['g_per_kwh']:.0f} gCO2e/kWh ({i['source']})" for r, i in report["intensity"].items()]
    lines += [
        "<details><summary>Assumptions and sources</summary>",
        "",
        f"- {report['assumptions']['label']}; CPU utilization {report['assumptions']['cpu_utilization']:.0%}.",
        *[f"- Grid intensity {n}" for n in region_notes],
        f"- {report['assumptions']['runner_region_note']}",
        "- Excluded from the total: " + (", ".join(s['name'] for s in report['skipped']) or "nothing"),
        "- Full method: `docs/METHODOLOGY.md`.",
        "",
        "</details>",
    ]
    if pipeline_url:
        lines += ["", f"Pipeline: {pipeline_url}"]
    return "\n".join(lines)


def verdict_note(*, passed: bool, saving_pct: float | None, duration_pct: float | None, reasons: list[str]) -> str:
    if passed:
        return (f"**Auto-merged by GreenGate:** {saving_pct:+.1f}% gCO2e, {duration_pct:+.1f}% runner time, "
                "policy OK, all jobs green. No human was needed.")
    return "**GreenGate stopped safely (needs-human):**\n\n" + "\n".join(f"- {r}" for r in reasons)
