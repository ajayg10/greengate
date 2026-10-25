from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from greengate.config import Intent, load_intent

ROOT = Path(__file__).resolve().parents[1]


def test_repo_intent_file_is_valid():
    intent = load_intent(ROOT / "greengate.yml")
    assert intent.primary_region == "eu-north-1"
    assert intent.canary_steps[-1] == 100
    assert "urgent" in intent.urgent_labels


def _base():
    return yaml.safe_load((ROOT / "greengate.yml").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "patch",
    [
        {"canary_steps": [10, 50]},
        {"canary_steps": [50, 10, 100]},
        {"canary_steps": [0, 100]},
        {"carbon_budget_g_per_pipeline": 0},
        {"slo": {"max_error_rate": 2, "max_p95_ms": 100}},
        {"allowed_regions": []},
        {"demo_defer_minutes": 999},
    ],
)
def test_invalid_intent_rejected(patch):
    data = _base() | patch
    with pytest.raises(ValidationError):
        Intent.model_validate(data)
