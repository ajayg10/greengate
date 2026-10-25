"""Decide whether to deploy now or defer to a lower-carbon window.

URGENT (merged MR has an urgent label)        -> DEPLOY_NOW
primary region intensity < threshold          -> DEPLOY_NOW
a greener allowed region is below threshold   -> DEPLOY_NOW in the primary, recommend that region (note)
deferred for max_defer_minutes already        -> DEPLOY_NOW (never block shipping forever)
otherwise                                     -> DEFER (EventBridge Scheduler re-triggers the pipeline later)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from greengate.config import Intent
from greengate.sci.intensity import Intensity


@dataclass
class Decision:
    action: str  # DEPLOY_NOW | DEFER
    region: str
    reason: str
    intensity: dict[str, Any]
    urgent: bool = False
    recommend_region: str | None = None
    defer_until: str | None = None
    first_deferred_at: str | None = None
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    def to_env(self) -> str:
        lines = {
            "GG_DEPLOY_DECISION": self.action,
            "GG_DEPLOY_REGION": self.region,
            "GG_DECISION_REASON": self.reason.replace("\n", " "),
            "GG_DEFER_UNTIL": self.defer_until or "",
            "GG_FIRST_DEFERRED_AT": self.first_deferred_at or "",
        }
        return "".join(f"{k}={v}\n" for k, v in lines.items())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide(*, intent: Intent, mr_labels: list[str], intensity_of, now: datetime | None = None,
           deferred: bool = False, first_deferred_at: str | None = None, demo_mode: bool = False) -> Decision:
    now = now or datetime.now(UTC)
    primary = intent.primary_region
    p: Intensity = intensity_of(primary)
    urgent = sorted(set(mr_labels) & set(intent.urgent_labels))
    if urgent:
        return Decision("DEPLOY_NOW", primary, f"urgent label(s) {', '.join(urgent)}: carbon deferral skipped",
                        p.to_dict(), urgent=True, first_deferred_at=first_deferred_at)
    if p.g_per_kwh < intent.carbon_threshold_g_per_kwh:
        return Decision("DEPLOY_NOW", primary,
                        f"{primary} grid at {p.g_per_kwh:.0f} gCO2e/kWh, below {intent.carbon_threshold_g_per_kwh:.0f}",
                        p.to_dict(), first_deferred_at=first_deferred_at)
    alternatives = []
    for region in intent.allowed_regions[1:]:
        alt = intensity_of(region)
        alternatives.append(alt.to_dict())
    greener = min((a for a in alternatives if a["g_per_kwh"] < intent.carbon_threshold_g_per_kwh),
                  key=lambda a: a["g_per_kwh"], default=None)
    if greener:
        return Decision("DEPLOY_NOW", primary,
                        f"{primary} is at {p.g_per_kwh:.0f} gCO2e/kWh but {greener['region']} is at "
                        f"{greener['g_per_kwh']:.0f}: deploying now, recommending {greener['region']}",
                        p.to_dict(), recommend_region=greener["region"], first_deferred_at=first_deferred_at,
                        alternatives=alternatives)
    started = datetime.fromisoformat(first_deferred_at) if first_deferred_at else now
    if deferred and now - started >= timedelta(minutes=intent.max_defer_minutes):
        return Decision("DEPLOY_NOW", primary, f"deferred for {intent.max_defer_minutes} min already: shipping anyway",
                        p.to_dict(), first_deferred_at=started.isoformat(), alternatives=alternatives)
    minutes = intent.demo_defer_minutes if demo_mode else min(60, intent.max_defer_minutes)
    until = now + timedelta(minutes=minutes)
    return Decision("DEFER", primary,
                    f"{primary} grid at {p.g_per_kwh:.0f} gCO2e/kWh is above {intent.carbon_threshold_g_per_kwh:.0f}"
                    f": re-checking in {minutes} min",
                    p.to_dict(), defer_until=until.isoformat(timespec="seconds"),
                    first_deferred_at=started.isoformat(timespec="seconds"), alternatives=alternatives)


def schedule_retrigger(scheduler: Any, *, decision: Decision, group: str, target_arn: str, role_arn: str,
                       project_id: str, ref: str, sha: str, pipeline_id: str) -> str:
    """One-time EventBridge Scheduler schedule that invokes gg-deferrer, which re-triggers this ref."""
    import json

    assert decision.defer_until
    at = datetime.fromisoformat(decision.defer_until).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    name = f"gg-defer-{pipeline_id}"
    scheduler.create_schedule(
        Name=name,
        GroupName=group,
        ScheduleExpression=f"at({at})",
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
        Target={
            "Arn": target_arn,
            "RoleArn": role_arn,
            "Input": json.dumps({"project_id": project_id, "ref": ref, "sha": sha,
                                 "first_deferred_at": decision.first_deferred_at,
                                 "deferred_from_pipeline": pipeline_id}),
        },
        Description=f"GreenGate deferred deploy of {sha[:8]} ({decision.reason})"[:512],
    )
    return name
