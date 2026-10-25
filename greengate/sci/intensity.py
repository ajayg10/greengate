"""Grid carbon intensity: Electricity Maps live data when a key is set, otherwise the cited static table."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

EM_URL = "https://api.electricitymap.org/v3/carbon-intensity/latest"
_cache: dict[str, Intensity] = {}


@dataclass(frozen=True)
class Intensity:
    region: str
    g_per_kwh: float
    source: str
    timestamp: str
    live: bool
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _static(region: str, coefficients: dict[str, Any]) -> Intensity:
    table = coefficients["grid_intensity"]
    if region not in table:
        raise KeyError(f"no grid intensity for region {region!r}; add it to coefficients.yaml")
    row = table[region]
    return Intensity(
        region=region,
        g_per_kwh=float(row["value"]),
        source=f"static: {row['source']} (accessed {row['accessed']})",
        timestamp=row["accessed"],
        live=False,
        verified=bool(row.get("verified", False)),
    )


def get_intensity(
    region: str,
    coefficients: dict[str, Any],
    *,
    api_key: str | None = None,
    client: httpx.Client | None = None,
    use_cache: bool = True,
    allow_force: bool = False,
) -> Intensity:
    # The demo override only ever applies to deploy decisions (allow_force=True), never to SCI measurements.
    forced = os.environ.get("GG_FORCE_INTENSITY") if allow_force else None
    if forced:
        return Intensity(region, float(forced), "demo override (GG_FORCE_INTENSITY), not a measurement",
                         datetime.now(UTC).isoformat(timespec="seconds"), live=False, verified=False)
    if use_cache and region in _cache:
        return _cache[region]
    api_key = api_key if api_key is not None else os.environ.get("ELECTRICITYMAPS_API_KEY", "")
    zone = coefficients.get("electricitymaps_zones", {}).get(region)
    result: Intensity | None = None
    if api_key and zone:
        try:
            c = client or httpx.Client(timeout=10.0)
            res = c.get(EM_URL, params={"zone": zone}, headers={"auth-token": api_key})
            res.raise_for_status()
            body = res.json()
            result = Intensity(region, float(body["carbonIntensity"]), f"Electricity Maps live, zone {zone}",
                               body.get("datetime", ""), live=True, verified=True)
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            result = None  # fall back to the static table; never block the pipeline on a third-party API
    if result is None:
        result = _static(region, coefficients)
    _cache[region] = result
    return result


def clear_cache() -> None:
    _cache.clear()
