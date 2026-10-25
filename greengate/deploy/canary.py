"""Lambda canary: publish a new version, send it a share of live traffic, probe it, promote or roll back.

Aliases:
  live    -> the public Function URL. Points at the stable version, plus weighted routing during a canary.
  canary  -> its own Function URL, always the newest version, so the probe hits only the new code.
The stable version is also stored in the ledger bucket (state/ecolinks-stable.json), the single source of truth.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CanaryOutcome:
    promoted: bool
    new_version: str
    stable_version: str
    rollback_seconds: float | None = None
    probe: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def wait_updated(lam: Any, function: str, *, timeout_s: float = 300, sleep=time.sleep) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        cfg = lam.get_function_configuration(FunctionName=function)
        if cfg.get("LastUpdateStatus") in (None, "Successful") and cfg.get("State") in (None, "Active"):
            return
        if cfg.get("LastUpdateStatus") == "Failed":
            raise RuntimeError(f"Lambda update failed: {cfg.get('LastUpdateStatusReason')}")
        sleep(3)
    raise TimeoutError("Lambda function did not finish updating")


def publish(lam: Any, *, function: str, image_uri: str, sha: str, sleep=time.sleep) -> str:
    lam.update_function_code(FunctionName=function, ImageUri=image_uri)
    wait_updated(lam, function, sleep=sleep)
    cfg = lam.get_function_configuration(FunctionName=function)
    env = (cfg.get("Environment") or {}).get("Variables", {})
    if env.get("GG_GIT_SHA") != sha:
        lam.update_function_configuration(FunctionName=function, Environment={"Variables": {**env, "GG_GIT_SHA": sha}})
        wait_updated(lam, function, sleep=sleep)
    return lam.publish_version(FunctionName=function, Description=f"GreenGate {sha[:12]}")["Version"]


def set_traffic(lam: Any, *, function: str, stable: str, new: str | None, weight: float) -> None:
    routing = {"AdditionalVersionWeights": {new: round(weight, 4)}} if new and 0 < weight < 1 else {}
    lam.update_alias(FunctionName=function, Name="live", FunctionVersion=stable, RoutingConfig=routing)


def promote(lam: Any, *, function: str, new: str) -> None:
    lam.update_alias(FunctionName=function, Name="live", FunctionVersion=new, RoutingConfig={})


def rollback(lam: Any, *, function: str, stable: str) -> None:
    lam.update_alias(FunctionName=function, Name="live", FunctionVersion=stable, RoutingConfig={})


def point_canary(lam: Any, *, function: str, new: str) -> None:
    lam.update_alias(FunctionName=function, Name="canary", FunctionVersion=new, RoutingConfig={})


def stable_version(lam: Any, ledger: Any, *, function: str) -> str:
    state = ledger.get_state(f"{function}-stable") if ledger else None
    if state and state.get("version"):
        return str(state["version"])
    return str(lam.get_alias(FunctionName=function, Name="live")["FunctionVersion"])


def run(*, lam: Any, ledger: Any, function: str, image_uri: str, sha: str, steps: list[int], probe_fn,
        slo_ok_fn, sleep=time.sleep, clock=time.time) -> CanaryOutcome:
    stable = stable_version(lam, ledger, function=function)
    new = publish(lam, function=function, image_uri=image_uri, sha=sha, sleep=sleep)
    if new == stable:
        # Same image and configuration as the live version (for example a re-run of the same commit):
        # Lambda returns the existing version, so there is nothing to canary.
        return CanaryOutcome(True, new, stable, steps=[{"note": "already live, no new version"}])
    point_canary(lam, function=function, new=new)
    outcome = CanaryOutcome(False, new, stable)
    for pct in steps:
        if pct >= 100:
            break
        set_traffic(lam, function=function, stable=stable, new=new, weight=pct / 100.0)
        result = probe_fn(new)
        outcome.steps.append({"traffic_pct": pct, "probe": result.to_dict()})
        outcome.probe = result.to_dict()
        if not slo_ok_fn(result):
            breach = result.breach_at or clock()
            rollback(lam, function=function, stable=stable)
            outcome.rollback_seconds = round(clock() - breach, 2)
            return outcome
    promote(lam, function=function, new=new)
    if ledger:
        ledger.put_state(f"{function}-stable", {"version": new, "sha": sha, "promoted_at": clock()})
    outcome.promoted = True
    return outcome


def bad_version_logs(logs: Any, *, function: str, version: str, limit: int = 50) -> list[str]:
    """Last log lines written by the given Lambda version (log stream names contain "[<version>]")."""
    group = f"/aws/lambda/{function}"
    try:
        streams = logs.describe_log_streams(logGroupName=group, orderBy="LastEventTime", descending=True,
                                            limit=20)["logStreams"]
    except Exception:  # log group may not exist yet
        return []
    lines: list[str] = []
    for s in streams:
        if f"[{version}]" not in s["logStreamName"]:
            continue
        events = logs.get_log_events(logGroupName=group, logStreamName=s["logStreamName"], limit=limit,
                                     startFromHead=False)["events"]
        lines += [e["message"].rstrip() for e in events]
        if len(lines) >= limit:
            break
    return lines[-limit:]
