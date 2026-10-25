"""Load and validate greengate.yml, the human intent file."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class Slo(BaseModel):
    max_error_rate: float = Field(ge=0, le=1)
    max_p95_ms: float = Field(gt=0)


class Probe(BaseModel):
    requests: int = Field(gt=0, le=1000)
    duration_s: float = Field(gt=0, le=600)


class Intent(BaseModel):
    carbon_budget_g_per_pipeline: float = Field(gt=0)
    sci_regression_tolerance_pct: float = Field(ge=0, le=100)
    slo: Slo
    urgent_labels: list[str] = Field(min_length=1)
    allowed_regions: list[str] = Field(min_length=1)
    carbon_threshold_g_per_kwh: float = Field(gt=0)
    max_defer_minutes: int = Field(ge=0)
    demo_defer_minutes: int = Field(ge=1)
    canary_steps: list[int] = Field(min_length=1)
    probe: Probe
    loop_guard_label: str = "greengate-auto"
    max_open_optimizer_mrs: int = Field(default=1, ge=1)

    @field_validator("canary_steps")
    @classmethod
    def _steps(cls, steps: list[int]) -> list[int]:
        if any(s <= 0 or s > 100 for s in steps) or steps != sorted(steps) or steps[-1] != 100:
            raise ValueError("canary_steps must be increasing percentages ending at 100")
        return steps

    @model_validator(mode="after")
    def _defer(self) -> Intent:
        if self.demo_defer_minutes > max(self.max_defer_minutes, 1):
            raise ValueError("demo_defer_minutes cannot exceed max_defer_minutes")
        return self

    @property
    def primary_region(self) -> str:
        return self.allowed_regions[0]


def load_intent(path: str | Path = "greengate.yml") -> Intent:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Intent.model_validate(data)
