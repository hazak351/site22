"""Estimativa e teto de créditos (tabela configurável em config/credits.json)."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .config import Settings
from .models import CreditPlan

DEFAULT_TABLE = {
    "modes": {
        "economico": {"label": "Econômico", "model_hint": "Veo - Fast", "model_match": "fast",
                      "credits_per_generation": 10, "max_attempts_per_scene": 1},
        "equilibrado": {"label": "Equilibrado", "model_hint": "Veo - Fast", "model_match": "fast",
                        "credits_per_generation": 20, "max_attempts_per_scene": 2},
        "premium": {"label": "Premium", "model_hint": "Veo - Quality", "model_match": "quality",
                    "credits_per_generation": 100, "max_attempts_per_scene": 2},
    },
    "default_max_credits_multiplier": 2.0,
}


class BudgetError(Exception):
    """Estimativa ou gasto acima do teto definido pelo usuário."""


def load_credit_table(settings: Settings) -> dict:
    path = Path(settings.config_dir) / "credits.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return DEFAULT_TABLE


def estimate(
    durations: list[float],
    mode: str,
    table: dict,
    outputs_per_prompt: int = 1,
    max_credits: int | None = None,
    max_attempts: int | None = None,
    simulated: bool = False,
) -> CreditPlan:
    modes = table.get("modes", {})
    if mode not in modes:
        raise ValueError(f"modo de qualidade desconhecido: {mode}")
    cfg = modes[mode]
    per_generation = int(cfg["credits_per_generation"]) * max(1, int(outputs_per_prompt))
    attempts = int(max_attempts or cfg.get("max_attempts_per_scene", 1))
    scenes = len(durations)
    estimated = scenes * per_generation
    worst_case = scenes * attempts * per_generation
    if max_credits is None:
        multiplier = float(table.get("default_max_credits_multiplier", 2.0))
        max_credits = max(estimated, min(worst_case, int(math.ceil(estimated * multiplier))))
    return CreditPlan(
        mode=mode,
        mode_label=cfg.get("label", mode),
        model_hint=cfg.get("model_hint", ""),
        credits_per_generation=per_generation,
        outputs_per_prompt=outputs_per_prompt,
        scenes=scenes,
        durations=durations,
        estimated=estimated,
        worst_case=worst_case,
        max_credits=int(max_credits),
        max_attempts_per_scene=attempts,
        simulated=simulated,
    )


def check_estimate(plan: CreditPlan) -> None:
    if plan.estimated > plan.max_credits:
        raise BudgetError(
            f"A estimativa ({plan.estimated} créditos) passa do teto definido ({plan.max_credits}). "
            "Aumente o teto, reduza a duração ou use um modo mais econômico."
        )


def can_spend(plan: CreditPlan, amount: int | None = None) -> bool:
    amount = plan.credits_per_generation if amount is None else amount
    return plan.used + amount <= plan.max_credits
