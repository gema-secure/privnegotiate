"""P5 可复现实验：基线与消融都能跑，且不违反硬约束。"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from app.simulation.baselines import STRATEGIES
from app.simulation.generators import InstanceParams, drift_instance, generate_instance
from app.simulation.responders import Policy, ResponderConfig, answer_batch
from app.simulation.runner import (
    ABLATIONS,
    ExperimentConfig,
    apply_ablation,
    run_instance,
    summarize,
)


def _config(round_limit: int = 1) -> ExperimentConfig:
    return ExperimentConfig(
        params=InstanceParams(members=3, candidates=13, exact_count=2),
        round_limit=round_limit,
        solver_time=0.2,
        batch_size=3,
        skip_rate=0.0,
    )


def test_generator_is_deterministic_and_validates() -> None:
    first = generate_instance(11)
    second = generate_instance(11)
    other = generate_instance(12)
    assert first.mu == second.mu
    assert first.costs_cents == second.costs_cents
    assert first.mu != other.mu
    assert all(len(row) == first.params.candidates for row in first.mu)
    with pytest.raises(ValueError):
        InstanceParams(candidates=40)
    with pytest.raises(ValueError):
        InstanceParams(members=2)
    with pytest.raises(ValueError):
        InstanceParams(pair_bonus=1.5)


def test_underreporting_changes_the_reported_labels() -> None:
    instance = generate_instance(3)
    displayed = instance.candidate_ids[:6]
    honest = answer_batch(
        instance, 1, displayed, ResponderConfig(Policy.HONEST, skip_rate=0.0), round_no=1, seed=3
    )
    selective = answer_batch(
        instance, 1, displayed, ResponderConfig(Policy.SELECTIVE, skip_rate=0.0), round_no=1, seed=3
    )
    blanket = answer_batch(
        instance, 2, displayed, ResponderConfig(Policy.BLANKET, skip_rate=0.0), round_no=1, seed=3
    )
    assert honest is not None and selective is not None and blanket is not None
    assert len(honest.evaluated_ids) == len(displayed)
    assert len(selective.acceptable_ids) <= 1
    assert len(blanket.acceptable_ids) <= 1


def test_all_baselines_run_without_violations() -> None:
    for strategy in STRATEGIES:
        result = run_instance(1, strategy, Policy.HONEST, _config())
        assert result.violations == (), strategy
        assert 0.0 <= result.regret <= 1.0, strategy
        assert result.questions > 0, strategy


def test_run_is_reproducible() -> None:
    first = run_instance(5, "heuristic", Policy.HONEST, _config(round_limit=2))
    second = run_instance(5, "heuristic", Policy.HONEST, _config(round_limit=2))
    assert first.as_dict() == second.as_dict()


def test_full_truth_optimum_is_reported_as_reference() -> None:
    result = run_instance(3, "heuristic", Policy.HONEST, _config())
    assert 0.0 <= result.average_utility <= 1.0
    assert 0.0 <= result.worst_utility <= result.average_utility
    assert result.optimum >= result.true_welfare - 1e-9


def test_all_ablations_run() -> None:
    for name in ABLATIONS:
        config = apply_ablation(_config(), name)
        result = run_instance(2, "heuristic", Policy.HONEST, config)
        assert result.violations == (), name
        assert 0.0 <= result.epsilon_safe <= 1.0, name


def test_all_policies_are_runnable() -> None:
    for policy in Policy:
        result = run_instance(4, "heuristic", policy, _config())
        assert result.solver_status in {"OPTIMAL", "FEASIBLE"}
        assert result.violations == ()


def test_summary_aggregates() -> None:
    rows = [
        run_instance(seed, "heuristic", Policy.HONEST, _config())
        for seed in (1, 2)
    ]
    summary = summarize(rows)
    assert summary["runs"] == 2
    assert 0.0 <= summary["mean_regret"] <= 1.0
    assert summary["violation_rate"] == 0.0


def test_drift_instance_is_deterministic_and_keeps_geometry() -> None:
    params = InstanceParams(members=4, candidates=20, exact_count=2)
    base = generate_instance(7, params)
    first = drift_instance(base, 7, fraction=0.5)
    second = drift_instance(base, 7, fraction=0.5)
    assert first.mu == second.mu
    assert first.costs_cents == base.costs_cents
    assert first.categories == base.categories
    assert first.mu != base.mu
    changed = sum(1 for old, new in zip(base.mu, first.mu) if old != new)
    assert changed == 2


def test_drift_run_is_reproducible_and_bound_is_not_applicable() -> None:
    config = replace(_config(round_limit=3), drift_round=2, drift_fraction=0.5)
    first = run_instance(7, "heuristic", Policy.HONEST, config)
    second = run_instance(7, "heuristic", Policy.HONEST, config)
    assert first.as_dict() == second.as_dict()
    assert first.bound_applicable is False
    assert len(first.bound_path) >= 2
    assert first.violations == ()


def test_pair_bonus_scenario_uses_enumeration_and_reports_regret() -> None:
    config = ExperimentConfig(
        params=InstanceParams(
            members=3, candidates=13, exact_count=2, pair_bonus=0.2
        ),
        round_limit=2,
        solver_time=0.2,
        batch_size=3,
        skip_rate=0.0,
    )
    result = run_instance(3, "heuristic", Policy.HONEST, config)
    assert result.optimum_source == "enumeration"
    assert result.bound_applicable is False
    assert result.regret >= 0.0
    assert 0.0 <= result.average_utility <= 1.0
    assert result.violations == ()


def test_question_budget_caps_total_displayed_items() -> None:
    config = replace(_config(round_limit=4), question_budget=7)
    result = run_instance(1, "heuristic", Policy.HONEST, config)
    assert 0 < result.questions <= 7
    assert result.violations == ()


def test_fixed_unknown_predictions_stay_at_prior() -> None:
    config = apply_ablation(_config(round_limit=2), "fixed_unknown")
    result = run_instance(1, "heuristic", Policy.HONEST, config)
    assert result.prediction_count > 0
    assert result.brier == pytest.approx(0.25, abs=1e-9)
    assert result.calibration[5][2] == result.prediction_count
    assert all(count == 0 for index, (_, _, count) in enumerate(result.calibration) if index != 5)


def test_prediction_quality_and_target_cost_are_recorded() -> None:
    result = run_instance(3, "heuristic", Policy.HONEST, _config(round_limit=3))
    assert result.prediction_count > 0
    assert result.brier is not None and 0.0 <= result.brier <= 1.0
    assert result.top_count > 0
    assert result.top_log_loss is not None and math.isfinite(result.top_log_loss)
    assert sum(count for _, _, count in result.calibration) == result.prediction_count
    assert result.target_round is None or result.target_round >= 1
    if result.target_questions is not None:
        assert 0 <= result.target_questions <= result.questions


def test_summary_includes_coverage_curve_and_solver_seconds() -> None:
    rows = [
        run_instance(seed, "heuristic", Policy.HONEST, _config(round_limit=3))
        for seed in (1, 2)
    ]
    summary = summarize(rows)
    curve = summary["coverage_curve"]
    assert curve and curve[0]["round"] == 1
    assert curve[0]["runs"] == 2
    assert 0.0 <= curve[0]["mean_coverage"] <= 1.0
    assert 0.0 <= curve[0]["mean_epsilon_safe"] <= 1.0
    assert summary["mean_solver_seconds"] >= 0.0
    assert summary["bound_applicability_rate"] == 1.0
    assert 0.0 <= summary["target_rate"] <= 1.0
    assert summary["prediction_count"] > 0
    assert summary["brier_score"] is not None
    assert 0.0 <= summary["brier_score"] <= 1.0
    assert summary["top_count"] > 0
    assert sum(bin["count"] for bin in summary["calibration_curve"]) == summary["prediction_count"]


def test_fixed_unknown_keeps_labels_and_falls_back_to_prior() -> None:
    """fixed_unknown 只把未评价项钉在 p0，不能把明确标签一起丢掉。"""
    from app.preference.acceptance import AnswerObservation
    from app.simulation.runner import estimate_bundle

    instance = generate_instance(5, InstanceParams(members=3, candidates=13, exact_count=2))
    answers = [
        AnswerObservation(
            member_id=1,
            evaluated_ids=("c01", "c02", "c03"),
            acceptable_ids=("c01", "c03"),
            top_id="c01",
        )
    ]
    config = apply_ablation(_config(round_limit=2), "fixed_unknown")
    bundle = estimate_bundle(instance, answers, config)
    row = dict(zip(bundle.estimate.candidate_ids, bundle.estimate.mu[0]))
    assert row["c01"] > 0.0 and row["c03"] > 0.0  # 标签被保留
    assert row["c02"] == 0.0
    # 未评价项 = p0 且与 w_top 无关的同一常数
    prior_only = dict(zip(bundle.estimate.candidate_ids, bundle.estimate.mu[1]))
    assert prior_only["c01"] == pytest.approx(prior_only["c02"])
    assert prior_only["c01"] > 0.0
    # 相对偏好被一并关闭：所有成员的 scale 不因作答而不同
    assert bundle.relative.members == {}


def test_flat_top_profile_uses_ratio_matched_bonus() -> None:
    from app.preference.acceptance import AnswerObservation
    from app.simulation.runner import estimate_bundle

    instance = generate_instance(5, InstanceParams(members=3, candidates=13, exact_count=2))
    answers = [
        AnswerObservation(
            member_id=1,
            evaluated_ids=("c01", "c02", "c03"),
            acceptable_ids=("c01", "c02"),
            top_id="c02",
        )
    ]
    config = replace(_config(round_limit=2), preference_profile="flat_top")
    bundle = estimate_bundle(instance, answers, config)
    w_top = bundle.estimate.w_top
    row = dict(zip(bundle.estimate.candidate_ids, bundle.estimate.mu[0]))
    assert row["c02"] == pytest.approx(1.0 + config.flat_top_ratio * w_top)
    assert row["c01"] == pytest.approx(1.0 + config.flat_accept_ratio * w_top)
    assert row["c03"] == 0.0
    assert row["c02"] > row["c01"] > 0.0
    # 未评价项仍走 Beta 均值，不被平顶项规则改写
    assert row["c04"] == pytest.approx(
        dict(zip(bundle.estimate.candidate_ids, bundle.estimate.mu[1]))["c04"]
    )


def test_preference_profile_is_validated() -> None:
    with pytest.raises(ValueError):
        replace(_config(), preference_profile="does_not_exist")
    with pytest.raises(ValueError):
        replace(_config(), flat_top_ratio=0.2, flat_accept_ratio=0.5)


def test_summary_reports_brier_constant_baseline_and_skill() -> None:
    rows = [
        run_instance(seed, "heuristic", Policy.HONEST, _config(round_limit=2))
        for seed in (1, 2)
    ]
    summary = summarize(rows)
    constant = summary["brier_constant_baseline"]
    assert constant is not None and 0.0 <= constant <= 0.25
    assert summary["accept_rate"] is not None
    assert summary["brier_skill_score"] == pytest.approx(
        1.0 - summary["brier_score"] / constant, abs=1e-5
    )


def test_manipulation_report_pairs_by_seed() -> None:
    from app.simulation.runner import manipulation_report

    config = _config(round_limit=2)
    seeds = (1, 2)
    honest = [run_instance(seed, "heuristic", Policy.HONEST, config) for seed in seeds]
    selective = [run_instance(seed, "heuristic", Policy.SELECTIVE, config) for seed in seeds]
    report = manipulation_report(honest, selective, member_id=1)
    assert report["runs"] == 2
    assert 0.0 <= report["benefit_rate"] <= 1.0
    assert report["min_gain"] <= report["median_gain"] <= report["max_gain"]
    assert report["mean_average_utility_honest"] > 0.0
    assert manipulation_report(honest, honest, member_id=1)["benefit_rate"] == 0.0
    assert manipulation_report(honest, [], member_id=1) == {}
