"""固定种子实验：同一实例、同一响应机制，对比基线与消融。"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

from app.constraints.compiler import ConstraintModel, compile_constraints
from app.constraints.dsl import (
    Candidate,
    ConfirmedConstraint,
    ConstraintKind,
    ConstraintScope,
    Member,
)
from app.diagnostics.safe_bound import BoundReport, SafeBoundConfig, compute_safe_bound
from app.preference.acceptance import (
    AcceptancePrior,
    AcceptanceState,
    AnswerObservation,
    rebuild_acceptance,
)
from app.preference.bounds import BoundConfig, build_bounds
from app.preference.estimates import UtilityConfig, UtilityEstimate, estimate_utilities
from app.preference.relative_choice import (
    RelativeChoiceState,
    RelativeChoicePrior,
    fit_relative_choice,
)
from app.scheduler.heuristic import (
    MemberQuestionPlan,
    QuestionPlan,
    SchedulerConfig,
)
from app.solver.challengers import ChallengeConfig, build_challengers
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.mean_fair import SolveStatus, SolverConfig
from app.simulation.baselines import make_plan
from app.simulation.generators import InstanceParams, SimInstance, drift_instance, generate_instance
from app.simulation.metrics import (
    enumerate_true_optimum,
    member_welfare,
    solve_true_optimum,
    true_objective,
    violations,
)
from app.simulation.responders import Policy, ResponderConfig, answer_batch
from types import MappingProxyType


PREFERENCE_PROFILES = ("pl_v0", "flat_top")
"""偏好估计 profile。

- pl_v0：现行实现，Beta 类别接受 + PL 集合顶项 MAP/Laplace + sigmoid 求积。
- flat_top：平顶项加分。顶项 1+ratio·w_top，其余明确接受 1+accept_ratio·w_top，
  未评价仍用 Beta 均值。默认 ratio=0.7 是 σ_v=1 下"批内最大值的
  E[sigmoid(v)]≈0.72"的矩匹配值，不是自由调参。
"""


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    params: InstanceParams = field(default_factory=InstanceParams)
    round_limit: int = 4
    lam: Fraction = Fraction(1, 4)
    solver_time: float = 0.5
    skip_rate: float = 0.05
    batch_size: int = 4
    eta: float = 0.05
    epsilon: float = 0.05
    rho_explore: float = 0.2
    beta: float = 0.5
    category_sharing: bool = True
    top_likelihood: bool = True
    fixed_unknown: bool = False
    preference_profile: str = "pl_v0"
    flat_top_ratio: float = 0.7
    flat_accept_ratio: float = 0.5
    include_personal: bool = True
    include_upper: bool = True
    include_diverse: bool = True
    drift_round: int = 0
    drift_fraction: float = 0.2
    question_budget: int = 0

    def __post_init__(self) -> None:
        if self.round_limit < 1:
            raise ValueError("轮数上限必须为正")
        if not 0 <= self.skip_rate <= 1:
            raise ValueError("跳过率必须在 [0,1] 内")
        if self.drift_round < 0:
            raise ValueError("漂移轮次不得为负")
        if not 0 < self.drift_fraction <= 1:
            raise ValueError("漂移成员比例必须在 (0,1] 内")
        if self.question_budget < 0:
            raise ValueError("总展示项预算不得为负")
        if self.preference_profile not in PREFERENCE_PROFILES:
            raise ValueError(
                f"未知偏好 profile：{self.preference_profile}，"
                f"可选 {sorted(PREFERENCE_PROFILES)}"
            )
        for name, value in (
            ("flat_top_ratio", self.flat_top_ratio),
            ("flat_accept_ratio", self.flat_accept_ratio),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} 必须在 [0,1] 内")
        if self.flat_top_ratio < self.flat_accept_ratio:
            raise ValueError("flat_top_ratio 不得小于 flat_accept_ratio")


@dataclass(frozen=True, slots=True)
class RunResult:
    seed: int
    strategy: str
    policy: str
    rounds: int
    final_ids: tuple[str, ...]
    true_welfare: float
    optimum: float
    regret: float
    average_utility: float
    worst_utility: float
    violations: tuple[str, ...]
    questions: int
    questions_by_member: tuple[tuple[int, int], ...]
    stable: bool
    stable_misjudgment: bool
    epsilon_safe: float
    bound_source: str
    coverage_ratio: float
    solver_status: str
    solver_seconds: float = 0.0
    bound_applicable: bool = True
    optimum_source: str = "cpsat"
    bound_path: tuple[tuple[float, float], ...] = ()
    target_questions: int | None = None
    target_round: int | None = None
    target_solver_seconds: float | None = None
    brier: float | None = None
    top_log_loss: float | None = None
    prediction_count: int = 0
    top_count: int = 0
    calibration: tuple[tuple[float, float, int], ...] = ()
    utilities: tuple[float, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "strategy": self.strategy,
            "policy": self.policy,
            "rounds": self.rounds,
            "final_ids": list(self.final_ids),
            "true_welfare": round(self.true_welfare, 6),
            "optimum": round(self.optimum, 6),
            "regret": round(self.regret, 6),
            "average_utility": round(self.average_utility, 6),
            "worst_utility": round(self.worst_utility, 6),
            "violations": list(self.violations),
            "questions": self.questions,
            "questions_by_member": {str(k): v for k, v in self.questions_by_member},
            "stable": self.stable,
            "stable_misjudgment": self.stable_misjudgment,
            "epsilon_safe": round(self.epsilon_safe, 6),
            "bound_source": self.bound_source,
            "coverage_ratio": round(self.coverage_ratio, 6),
            "solver_status": self.solver_status,
            "bound_applicable": self.bound_applicable,
            "optimum_source": self.optimum_source,
            "bound_path": [
                {"coverage": round(coverage, 6), "epsilon_safe": round(epsilon, 6)}
                for coverage, epsilon in self.bound_path
            ],
            "target_questions": self.target_questions,
            "target_round": self.target_round,
            "brier": None if self.brier is None else round(self.brier, 6),
            "top_log_loss": None if self.top_log_loss is None else round(self.top_log_loss, 6),
            "prediction_count": self.prediction_count,
            "top_count": self.top_count,
            "calibration": [
                {
                    "bin": index,
                    "predicted": round(predicted / count, 6),
                    "observed": round(observed / count, 6),
                    "count": count,
                }
                for index, (predicted, observed, count) in enumerate(self.calibration)
                if count
            ],
            "utilities": [round(value, 6) for value in self.utilities],
        }


ABLATIONS: Mapping[str, ExperimentConfig] = {}


def ablation_overrides() -> dict[str, dict[str, Any]]:
    """消融项的唯一真源；ABLATIONS 与 apply_ablation 都从这里派生，避免两处漂移。"""
    return {
        "lam0": {"lam": Fraction(0)},
        "lam5": {"lam": Fraction(1, 2)},
        "lam75": {"lam": Fraction(3, 4)},
        "no_category_sharing": {"category_sharing": False},
        "no_top_likelihood": {"top_likelihood": False},
        "flat_top": {"preference_profile": "flat_top"},
        "fixed_unknown": {"fixed_unknown": True},
        "no_personal_challenger": {"include_personal": False},
        "no_interval_challenger": {"include_upper": False},
        "no_exploration": {"rho_explore": 0.0},
        "no_load": {"beta": 0.0},
    }


def ablation_names() -> tuple[str, ...]:
    return tuple(ablation_overrides())


def _register_ablations() -> None:
    global ABLATIONS
    default = ExperimentConfig()
    ABLATIONS = {
        name: replace(default, **overrides)
        for name, overrides in ablation_overrides().items()
    }


_register_ablations()


def apply_ablation(config: ExperimentConfig, name: str) -> ExperimentConfig:
    if name == "none":
        return config
    overrides = ablation_overrides()
    if name not in overrides:
        raise ValueError(f"未知消融项：{name}，可选 {sorted(overrides)} 或 none")
    return replace(config, **overrides[name])


def build_model(instance: SimInstance) -> ConstraintModel:
    candidates = tuple(
        Candidate(
            candidate_id=candidate_id,
            cost_cents=instance.costs_cents[index],
            category=instance.categories[index],
        )
        for index, candidate_id in enumerate(instance.candidate_ids)
    )
    members = tuple(Member(member_id=member_id) for member_id in instance.member_ids)
    constraints = [
        ConfirmedConstraint(
            constraint_id="budget",
            kind=ConstraintKind.BUDGET,
            scope=ConstraintScope.PUBLIC,
            amount_cents=instance.budget_cents,
            confirmed_version=1,
        ),
        ConfirmedConstraint(
            constraint_id="exact",
            kind=ConstraintKind.EXACT_COUNT,
            scope=ConstraintScope.PUBLIC,
            min_count=instance.exact_count,
            confirmed_version=1,
        ),
    ]
    by_member: dict[int, list[str]] = {}
    for member_id, candidate_id in instance.opt_out:
        by_member.setdefault(member_id, []).append(candidate_id)
    for member_id, candidate_ids in by_member.items():
        constraints.append(
            ConfirmedConstraint(
                constraint_id=f"optout-{member_id}",
                kind=ConstraintKind.OPT_OUT,
                scope=ConstraintScope.MEMBER,
                owner_member_id=member_id,
                candidate_ids=tuple(candidate_ids),
                confirmed_version=1,
            )
        )
    return compile_constraints(constraints, candidates, members)


def _opt_out_map(instance: SimInstance) -> dict[int, tuple[str, ...]]:
    result: dict[int, list[str]] = {}
    for member_id, candidate_id in instance.opt_out:
        result.setdefault(member_id, []).append(candidate_id)
    return {key: tuple(value) for key, value in result.items()}


@dataclass(frozen=True, slots=True)
class EstimateBundle:
    estimate: UtilityEstimate
    acceptance: AcceptanceState
    relative: RelativeChoiceState


def estimate_bundle(
    instance: SimInstance,
    answers: Sequence[AnswerObservation],
    config: ExperimentConfig,
) -> EstimateBundle:
    """从已接受作答重建估计。

    fixed_unknown 的语义是"保留明确标签，只把未评价项的预测钉成先验"，
    不是"丢掉全部作答"——后者会把基线退化成无信息对照，无法回答问题。
    """
    categories = dict(zip(instance.candidate_ids, instance.categories))
    if not config.category_sharing:
        categories = {candidate_id: candidate_id for candidate_id in instance.candidate_ids}
    acceptance = rebuild_acceptance(list(answers), categories, AcceptancePrior())
    if config.fixed_unknown:
        # 清空类别计数：p_and_variance 对明确标签仍返回 1/0，对未评价项退回 prior.p0。
        acceptance = AcceptanceState(
            candidate_categories=acceptance.candidate_categories,
            labels=acceptance.labels,
            category_states=MappingProxyType({}),
            prior=acceptance.prior,
            member_ids=acceptance.member_ids,
        )
    if config.top_likelihood and not config.fixed_unknown:
        relative = fit_relative_choice(list(answers), RelativeChoicePrior())
    else:
        relative = RelativeChoiceState(members=MappingProxyType({}), prior_variance=1.0)
    estimate = estimate_utilities(
        acceptance,
        relative,
        _opt_out_map(instance),
        UtilityConfig(w_top=instance.params.w_top),
        0,
        extra_member_ids=instance.member_ids,
    )
    if config.preference_profile == "flat_top":
        estimate = _apply_flat_top(estimate, acceptance, answers, config)
    return EstimateBundle(estimate=estimate, acceptance=acceptance, relative=relative)


def _apply_flat_top(
    estimate: UtilityEstimate,
    acceptance: AcceptanceState,
    answers: Sequence[AnswerObservation],
    config: ExperimentConfig,
) -> UtilityEstimate:
    """把 PL 推出来的顶项强度替换成批内矩匹配的平顶项加分。"""
    w_top = estimate.w_top
    index_of = {candidate_id: index for index, candidate_id in enumerate(estimate.candidate_ids)}
    row_of = {member_id: index for index, member_id in enumerate(estimate.member_ids)}
    rows = [list(row) for row in estimate.mu]
    for (member_id, candidate_id), accepted in acceptance.labels.items():
        row = row_of.get(member_id)
        column = index_of.get(candidate_id)
        if not accepted or row is None or column is None:
            continue
        rows[row][column] = 1.0 + config.flat_accept_ratio * w_top
    for answer in answers:
        if answer.mode != "explicit" or answer.top_id is None:
            continue
        row = row_of.get(answer.member_id)
        column = index_of.get(answer.top_id)
        if row is None or column is None:
            continue
        if acceptance.labels.get((answer.member_id, answer.top_id)) is not True:
            continue
        rows[row][column] = 1.0 + config.flat_top_ratio * w_top
    return replace(estimate, mu=tuple(tuple(row) for row in rows))



def estimate_for(
    instance: SimInstance,
    answers: Sequence[AnswerObservation],
    config: ExperimentConfig,
) -> UtilityEstimate:
    return estimate_bundle(instance, answers, config).estimate


def _aligned_mu(estimate: UtilityEstimate, instance: SimInstance) -> tuple[tuple[float, ...], ...]:
    rows = {
        member_id: dict(zip(estimate.candidate_ids, row))
        for member_id, row in zip(estimate.member_ids, estimate.mu)
    }
    return tuple(
        tuple(rows[member_id][candidate_id] for candidate_id in instance.candidate_ids)
        for member_id in instance.member_ids
    )


def _solver_config(instance: SimInstance, config: ExperimentConfig, version: int = 1) -> SolverConfig:
    return SolverConfig(
        lam=config.lam,
        w_top=instance.params.w_top,
        quantization_scale=1000,
        time_limit_seconds=config.solver_time,
        num_search_workers=1,
        random_seed=0,
        config_version=version,
    )


def _scheduler_config(config: ExperimentConfig) -> SchedulerConfig:
    return SchedulerConfig(
        batch_size=config.batch_size,
        eta=config.eta,
        epsilon=config.epsilon,
        rho_explore=config.rho_explore,
        beta=config.beta,
        use_load=config.beta > 0,
    )


CALIBRATION_BINS = 10


def _snapshot(
    bundle: EstimateBundle, instance: SimInstance
) -> tuple[dict[tuple[int, str], float], dict[tuple[int, str], float]]:
    """本轮作答前的预测快照；评分时只用当时未知、随后被回答的项。"""
    accept: dict[tuple[int, str], float] = {}
    strength: dict[tuple[int, str], float] = {}
    for member_id in instance.member_ids:
        for candidate_id in instance.candidate_ids:
            accept[(member_id, candidate_id)] = bundle.acceptance.p_and_variance(
                member_id, candidate_id
            )[0]
            strength[(member_id, candidate_id)] = bundle.relative.posterior(
                member_id, candidate_id
            )[0]
    return accept, strength


def _top_log_loss(
    strength: Mapping[tuple[int, str], float],
    member_id: int,
    acceptable: Sequence[str],
    top_id: str,
) -> float | None:
    values = [strength[(member_id, candidate_id)] for candidate_id in acceptable]
    if not values:
        return None
    maximum = max(values)
    total = sum(math.exp(value - maximum) for value in values)
    probability = math.exp(strength[(member_id, top_id)] - maximum) / total
    return -math.log(min(1.0, max(probability, 1e-12)))


def _cap_plan(plan: QuestionPlan, remaining: int) -> QuestionPlan:
    """按剩余总展示项预算裁剪本轮问题包。"""
    if remaining <= 0:
        return replace(plan, members=(), fallback_reason="QUESTION_BUDGET_EXHAUSTED")
    members: list[MemberQuestionPlan] = []
    left = remaining
    for member_plan in plan.members:
        if left <= 0:
            break
        items = member_plan.items[:left]
        if not items:
            continue
        members.append(replace(member_plan, items=items))
        left -= len(items)
    return replace(
        plan,
        members=tuple(members),
        fallback_reason=plan.fallback_reason if members else "QUESTION_BUDGET_EXHAUSTED",
    )


def run_instance(
    seed: int,
    strategy: str,
    policy: Policy,
    config: ExperimentConfig | None = None,
) -> RunResult:
    settings = config or ExperimentConfig()
    instance = generate_instance(seed, settings.params)
    truth = instance
    model = build_model(instance)
    solver = _solver_config(instance, settings)
    challenge = ChallengeConfig(
        include_personal=settings.include_personal,
        include_upper=settings.include_upper,
        include_diverse=settings.include_diverse,
        time_limit_seconds=settings.solver_time,
    )
    scheduler = _scheduler_config(settings)
    responder = ResponderConfig(policy=policy, skip_rate=settings.skip_rate)
    bound_applicable = instance.pair_bonus == 0.0 and settings.drift_round == 0

    answers: list[AnswerObservation] = []
    questions: dict[int, int] = {member_id: 0 for member_id in instance.member_ids}
    previous: tuple[str, ...] | None = None
    stable_run = 1
    stable = False
    rounds = 0
    epsilon = 1.0
    source = "universal"
    coverage = 0.0
    final_ids: tuple[str, ...] = ()
    status = ""
    solver_seconds = 0.0
    bound_path: list[tuple[float, float]] = []
    asked = 0
    brier_sum = 0.0
    brier_n = 0
    top_loss_sum = 0.0
    top_n = 0
    calibration_pred = [0.0] * CALIBRATION_BINS
    calibration_obs = [0.0] * CALIBRATION_BINS
    calibration_n = [0] * CALIBRATION_BINS
    target_questions: int | None = None
    target_round: int | None = None
    target_solver_seconds: float | None = None
    optimum_value: float | None = None
    optimum_truth: SimInstance | None = None
    optimum_source = "cpsat"
    reference_solver = replace(
        solver, time_limit_seconds=max(1.0, settings.solver_time)
    )

    def reference_optimum(current_truth: SimInstance) -> float | None:
        nonlocal optimum_value, optimum_truth, optimum_source, solver_seconds
        if optimum_truth is current_truth and optimum_value is not None:
            return optimum_value
        value: float | None = None
        source = "cpsat"
        if current_truth.pair_bonus > 0:
            enumerated = enumerate_true_optimum(
                current_truth, model, float(settings.lam)
            )
            if enumerated is not None:
                value = enumerated.value
                source = enumerated.source
        if value is None:
            solved = solve_true_optimum(current_truth, model, reference_solver)
            solver_seconds += solved.wall_time or 0.0
            if solved.objective_float is not None:
                value = float(solved.objective_float)
                source = "cpsat"
        optimum_truth = current_truth
        optimum_value = value
        optimum_source = source if value is not None else "incumbent"
        return value

    estimated_answer_count = -1
    estimate = None
    for round_no in range(1, settings.round_limit + 1):
        if settings.drift_round and round_no == settings.drift_round:
            truth = drift_instance(instance, seed, settings.drift_fraction)
        bundle = estimate_bundle(instance, answers, settings)
        estimate = bundle.estimate
        estimated_answer_count = len(answers)
        mu = _aligned_mu(estimate, instance)
        result = solve_mean_fair(mu, model, solver)
        solver_seconds += result.wall_time or 0.0
        if result.status not in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE):
            status = result.status.value
            break
        final_ids = tuple(result.candidate_ids)
        status = result.status.value

        if target_questions is None:
            reference = reference_optimum(truth)
            if reference is not None:
                round_value = true_objective(truth, model, final_ids, float(settings.lam))
                if reference - round_value <= 0.05:
                    target_questions = asked
                    target_round = round_no
                    target_solver_seconds = solver_seconds

        bounds = build_bounds(
            answers,
            _opt_out_map(instance),
            BoundConfig(w_top=instance.params.w_top),
            candidate_ids=model.candidate_ids,
            member_ids=model.member_ids,
        )
        pool = build_challengers(
            mu,
            bounds,
            model,
            final_ids,
            solver,
            challenge_config=challenge,
            previous_incumbent=previous,
        )
        solver_seconds += pool.wall_time
        report = compute_safe_bound(
            bounds,
            final_ids,
            model,
            solver,
            SafeBoundConfig(w_top=instance.params.w_top, time_limit_seconds=settings.solver_time),
            upper_bound=pool.upper_bound,
            upper_status=pool.upper_status or None,
            upper_quantization_bound=pool.upper_quantization_bound,
        )
        plan = make_plan(
            strategy,
            estimate,
            pool,
            answers,
            model,
            scheduler,
            seed=seed,
            round_no=round_no,
        )
        rounds = round_no
        epsilon = report.epsilon_safe
        source = report.bound_source
        coverage = report.coverage_ratio
        bound_path.append((coverage, epsilon))

        if settings.question_budget:
            plan = _cap_plan(plan, settings.question_budget - asked)
        if not plan.members:
            break
        snapshot_accept, snapshot_strength = _snapshot(bundle, instance)
        for member_plan in plan.members:
            displayed = tuple(item.candidate_id for item in member_plan.items)
            if not displayed:
                continue
            observation = answer_batch(
                truth,
                member_plan.member_id,
                displayed,
                responder,
                round_no=round_no,
                seed=seed,
            )
            if observation is not None:
                answers.append(observation)
                acceptable = set(observation.acceptable_ids)
                for candidate_id in observation.evaluated_ids:
                    probability = snapshot_accept[(member_plan.member_id, candidate_id)]
                    label = 1.0 if candidate_id in acceptable else 0.0
                    brier_sum += (probability - label) ** 2
                    brier_n += 1
                    bin_index = min(CALIBRATION_BINS - 1, int(probability * CALIBRATION_BINS))
                    calibration_pred[bin_index] += probability
                    calibration_obs[bin_index] += label
                    calibration_n[bin_index] += 1
                if observation.top_id is not None and len(observation.acceptable_ids) >= 2:
                    loss = _top_log_loss(
                        snapshot_strength,
                        member_plan.member_id,
                        observation.acceptable_ids,
                        observation.top_id,
                    )
                    if loss is not None:
                        top_loss_sum += loss
                        top_n += 1
            questions[member_plan.member_id] += len(displayed)
            asked += len(displayed)

        if previous == final_ids:
            stable_run += 1
        else:
            stable_run = 1
        previous = final_ids
        if stable_run >= 2:
            stable = True
            break

    # The last round may have appended answers after its estimate was fitted.
    if estimate is None or estimated_answer_count != len(answers):
        estimate = estimate_for(instance, answers, settings)
    final_result = solve_mean_fair(_aligned_mu(estimate, instance), model, solver)
    solver_seconds += final_result.wall_time or 0.0
    if final_result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE):
        final_ids = tuple(final_result.candidate_ids)
        status = final_result.status.value

    if not final_ids:
        final_ids = tuple(instance.candidate_ids[: model.exact_count])
    true_value = true_objective(truth, model, final_ids, float(settings.lam))

    optimum = reference_optimum(truth)
    if optimum is None:
        optimum = true_value
        optimum_source = "incumbent"
    regret = max(0.0, optimum - true_value)
    if target_questions is None and optimum - true_value <= 0.05:
        target_questions = sum(questions.values())
        target_round = rounds if rounds else 1
        target_solver_seconds = solver_seconds

    utilities = member_welfare(
        truth.mu,
        model.selection_indices(final_ids),
        model.exact_count,
        truth.params.w_top,
        pair_bonus=truth.pair_bonus,
        categories=model.categories,
    )
    average_utility = sum(utilities) / len(utilities) if utilities else 0.0
    worst_utility = min(utilities) if utilities else 0.0
    return RunResult(
        seed=seed,
        strategy=strategy,
        policy=policy.value,
        rounds=rounds,
        final_ids=final_ids,
        true_welfare=true_value,
        optimum=optimum,
        regret=regret,
        average_utility=average_utility,
        worst_utility=worst_utility,
        violations=violations(model, final_ids),
        questions=sum(questions.values()),
        questions_by_member=tuple(sorted(questions.items())),
        stable=stable,
        stable_misjudgment=stable and regret > 0.05,
        epsilon_safe=epsilon,
        bound_source=source,
        coverage_ratio=coverage,
        solver_status=status,
        solver_seconds=solver_seconds,
        bound_applicable=bound_applicable,
        optimum_source=optimum_source,
        bound_path=tuple(bound_path),
        target_questions=target_questions,
        target_round=target_round,
        target_solver_seconds=target_solver_seconds,
        brier=brier_sum / brier_n if brier_n else None,
        top_log_loss=top_loss_sum / top_n if top_n else None,
        prediction_count=brier_n,
        top_count=top_n,
        calibration=tuple(zip(calibration_pred, calibration_obs, calibration_n)),
        utilities=tuple(utilities),
    )


def summarize(results: Iterable[RunResult]) -> dict[str, Any]:
    rows = list(results)
    if not rows:
        return {}
    regrets = [row.regret for row in rows]
    by_round: dict[int, list[tuple[float, float]]] = {}
    for row in rows:
        for round_no, (coverage, epsilon) in enumerate(row.bound_path, start=1):
            by_round.setdefault(round_no, []).append((coverage, epsilon))
    coverage_curve = [
        {
            "round": round_no,
            "runs": len(values),
            "mean_coverage": round(statistics.mean(coverage for coverage, _ in values), 6),
            "mean_epsilon_safe": round(statistics.mean(epsilon for _, epsilon in values), 6),
        }
        for round_no, values in sorted(by_round.items())
    ]
    reached = [row for row in rows if row.target_questions is not None]
    target_items = [row.target_questions for row in reached]
    brier_n = sum(row.prediction_count for row in rows)
    brier = (
        sum((row.brier or 0.0) * row.prediction_count for row in rows) / brier_n
        if brier_n
        else None
    )
    top_n = sum(row.top_count for row in rows)
    top_loss = (
        sum((row.top_log_loss or 0.0) * row.top_count for row in rows) / top_n
        if top_n
        else None
    )
    cal_pred = [0.0] * CALIBRATION_BINS
    cal_obs = [0.0] * CALIBRATION_BINS
    cal_n = [0] * CALIBRATION_BINS
    for row in rows:
        for index, (predicted, observed, count) in enumerate(row.calibration):
            cal_pred[index] += predicted
            cal_obs[index] += observed
            cal_n[index] += count
    calibration_curve = [
        {
            "bin": index,
            "predicted": round(cal_pred[index] / cal_n[index], 6),
            "observed": round(cal_obs[index] / cal_n[index], 6),
            "count": cal_n[index],
        }
        for index in range(CALIBRATION_BINS)
        if cal_n[index]
    ]
    # 接受率的边际基线：恒定预测总体接受率时的 Brier。预测器若不能显著低于它，
    # 就不具备样本外技能（§15.4 要求校准曲线，本项是对应的判据）。
    total_predictions = sum(cal_n)
    accept_rate = (
        sum(cal_obs) / total_predictions if total_predictions else None
    )
    brier_constant = (
        accept_rate * (1.0 - accept_rate)
        if accept_rate is not None
        else None
    )
    brier_skill = (
        1.0 - brier / brier_constant
        if brier is not None and brier_constant and brier_constant > 0
        else None
    )
    return {
        "runs": len(rows),
        "mean_regret": round(statistics.mean(regrets), 6),
        "max_regret": round(max(regrets), 6),
        "mean_welfare": round(statistics.mean(row.true_welfare for row in rows), 6),
        "mean_average_utility": round(statistics.mean(row.average_utility for row in rows), 6),
        "mean_worst_utility": round(statistics.mean(row.worst_utility for row in rows), 6),
        "stable_misjudgment_rate": round(
            sum(1 for row in rows if row.stable_misjudgment) / len(rows), 6
        ),
        "mean_questions": round(statistics.mean(row.questions for row in rows), 3),
        "violation_rate": round(sum(1 for row in rows if row.violations) / len(rows), 6),
        "mean_epsilon_safe": round(statistics.mean(row.epsilon_safe for row in rows), 6),
        "mean_coverage": round(statistics.mean(row.coverage_ratio for row in rows), 6),
        "stable_rate": round(sum(1 for row in rows if row.stable) / len(rows), 6),
        "mean_solver_seconds": round(
            statistics.mean(row.solver_seconds for row in rows), 4
        ),
        "bound_applicability_rate": round(
            sum(1 for row in rows if row.bound_applicable) / len(rows), 6
        ),
        "coverage_curve": coverage_curve,
        "target_rate": round(len(reached) / len(rows), 6),
        "mean_target_questions": round(statistics.mean(target_items), 3)
        if target_items
        else None,
        "median_target_questions": round(statistics.median(target_items), 3)
        if target_items
        else None,
        "mean_target_round": round(
            statistics.mean(row.target_round or 0 for row in reached), 3
        )
        if reached
        else None,
        "mean_target_solver_seconds": round(
            statistics.mean(row.target_solver_seconds or 0.0 for row in reached), 4
        )
        if reached
        else None,
        "brier_score": None if brier is None else round(brier, 6),
        "brier_constant_baseline": None if brier_constant is None else round(brier_constant, 6),
        "brier_skill_score": None if brier_skill is None else round(brier_skill, 6),
        "accept_rate": None if accept_rate is None else round(accept_rate, 6),
        "top_log_loss": None if top_loss is None else round(top_loss, 6),
        "prediction_coverage": round(
            sum(1 for row in rows if row.prediction_count) / len(rows), 6
        ),
        "prediction_count": brier_n,
        "top_count": top_n,
        "calibration_curve": calibration_curve,
    }


def manipulation_report(
    honest: Sequence[RunResult],
    selective: Sequence[RunResult],
    *,
    member_id: int = 1,
    tolerance: float = 1e-9,
) -> dict[str, Any]:
    """§6.1 要求的"被操纵实例比例"：同一实例、同一 seed 的诚实/低报配对比较。

    只看被低报成员本人的真实效用是否高于其诚实作答时的真实效用；
    另给群体层面的平均/最差效用与遗憾偏移，用于说明外部性。
    """
    by_seed = {row.seed: row for row in selective}
    index = member_id - 1
    gains: list[float] = []
    regret_deltas: list[float] = []
    honest_average: list[float] = []
    selective_average: list[float] = []
    honest_worst: list[float] = []
    selective_worst: list[float] = []
    for base in honest:
        other = by_seed.get(base.seed)
        if other is None:
            continue
        if len(base.utilities) <= index or len(other.utilities) <= index:
            continue
        gains.append(other.utilities[index] - base.utilities[index])
        regret_deltas.append(other.regret - base.regret)
        honest_average.append(base.average_utility)
        selective_average.append(other.average_utility)
        honest_worst.append(base.worst_utility)
        selective_worst.append(other.worst_utility)
    if not gains:
        return {}
    ordered = sorted(gains)
    return {
        "member_id": member_id,
        "runs": len(gains),
        "benefit_rate": round(
            sum(1 for gain in gains if gain > tolerance) / len(gains), 6
        ),
        "median_gain": round(statistics.median(ordered), 6),
        "mean_gain": round(statistics.mean(gains), 6),
        "max_gain": round(ordered[-1], 6),
        "min_gain": round(ordered[0], 6),
        "mean_regret_delta": round(statistics.mean(regret_deltas), 6),
        "mean_average_utility_honest": round(statistics.mean(honest_average), 6),
        "mean_average_utility_selective": round(statistics.mean(selective_average), 6),
        "mean_worst_utility_honest": round(statistics.mean(honest_worst), 6),
        "mean_worst_utility_selective": round(statistics.mean(selective_worst), 6),
    }
