import json

import pytest

from adflow.credits import BudgetError, can_spend, check_estimate, estimate, load_credit_table
from adflow.planner import generation_duration_for, plan_durations


@pytest.mark.parametrize("total,expected", [
    (6, [6.0]), (10, [5.0, 5.0]), (15, [8.0, 7.0]), (20, [7.0, 7.0, 6.0]), (24, [8.0, 8.0, 8.0]),
    (30, [8.0, 8.0, 7.0, 7.0]), (45, [8.0, 8.0, 8.0, 7.0, 7.0, 7.0]), (60, [8.0] * 4 + [7.0] * 4),
    (12.5, [6.0, 6.5]), (8, [8.0]),
])
def test_plan_durations_sum_and_limits(total, expected):
    durations = plan_durations(total, [8.0])
    assert durations == expected
    assert abs(sum(durations) - total) < 1e-6
    assert all(d <= 8.0 for d in durations)


def test_plan_durations_respects_min_scene():
    assert plan_durations(3, [8.0], min_scene=2.0) == [3.0]
    assert all(d >= 2.0 for d in plan_durations(9, [4.0], min_scene=2.0))


def test_plan_durations_rejects_invalid():
    with pytest.raises(ValueError):
        plan_durations(0, [8.0])
    with pytest.raises(ValueError):
        plan_durations(301, [8.0])


def test_generation_duration_picks_smallest_that_fits():
    assert generation_duration_for(6, [4.0, 6.0, 8.0]) == 6.0
    assert generation_duration_for(7, [4.0, 6.0, 8.0]) == 8.0
    assert generation_duration_for(3, [4.0, 6.0, 8.0]) == 4.0


def test_estimate_matches_user_example(settings):
    table = load_credit_table(settings)
    plan = estimate([8.0, 8.0, 8.0], "economico", table)
    assert plan.scenes == 3
    assert plan.estimated == 3 * table["modes"]["economico"]["credits_per_generation"]
    assert plan.max_credits >= plan.estimated


def test_estimate_outputs_per_prompt_multiplies_cost():
    table = {"modes": {"x": {"credits_per_generation": 20, "max_attempts_per_scene": 2}}}
    plan = estimate([8.0, 8.0], "x", table, outputs_per_prompt=2)
    assert plan.credits_per_generation == 40
    assert plan.estimated == 80
    assert plan.worst_case == 160


def test_budget_ceiling_is_enforced():
    table = {"modes": {"x": {"credits_per_generation": 20, "max_attempts_per_scene": 2}}}
    plan = estimate([8.0, 8.0, 8.0], "x", table, max_credits=50)
    with pytest.raises(BudgetError):
        check_estimate(plan)
    plan = estimate([8.0, 8.0, 8.0], "x", table, max_credits=70)
    check_estimate(plan)
    plan.used = 60
    assert not can_spend(plan)  # 60 + 20 > 70
    plan.used = 50
    assert can_spend(plan)


def test_credit_table_is_configurable(settings, tmp_path):
    custom = tmp_path / "cfg"
    custom.mkdir()
    (custom / "credits.json").write_text(json.dumps(
        {"modes": {"premium": {"credits_per_generation": 999, "max_attempts_per_scene": 3}}}), encoding="utf-8")
    settings.config_dir = custom
    plan = estimate([8.0], "premium", load_credit_table(settings))
    assert plan.estimated == 999
    assert plan.max_attempts_per_scene == 3


def test_unknown_mode_rejected(settings):
    with pytest.raises(ValueError):
        estimate([8.0], "ultra", load_credit_table(settings))
