"""算法行为探针：检查效用刻度、顶项信号强度、λ 的实际影响、共形化范围。"""
from __future__ import annotations
import sys, math, itertools, random
sys.path.insert(0, "backend")

from app.preference.acceptance import AnswerObservation, rebuild_acceptance, AcceptancePrior
from app.preference.relative_choice import fit_relative_choice, RelativeChoicePrior
from app.preference.estimates import estimate_utilities, UtilityConfig
from app.preference.bounds import build_bounds

CATS = {f"c{i}": ("A" if i % 2 == 0 else "B") for i in range(1, 13)}


def show(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ---------- 1. 顶项信号到底把 mu 推多远 ----------
show("1. 顶项信号对 mu 的实际影响（w_top=1, sigma_v=1）")
ans = [
    AnswerObservation(member_id=1, evaluated_ids=("c1","c2","c3","c4"),
                      acceptable_ids=("c1","c2","c3","c4"), top_id="c1"),
]
acc = rebuild_acceptance(ans, CATS)
rel = fit_relative_choice(ans)
est = estimate_utilities(acc, rel, {}, UtilityConfig())
row = dict(zip(est.candidate_ids, est.mu[0]))
print(f"  一次 4 项全接受 + 顶 c1：")
for c in ("c1","c2","c3","c4"):
    print(f"    {c}: mu={row[c]:.4f}")
print(f"    顶项 vs 非顶项 差距 = {row['c1']-row['c2']:.4f}  "
      f"(理论最大差距 w_top=1 -> 1.0)")
print(f"    未评价项 c5: mu={row['c5']:.4f}   (类别 B 先验 0.5*1.5=0.75)")

# 多轮累积
ans2 = list(ans) + [
    AnswerObservation(member_id=1, evaluated_ids=("c1","c2"), acceptable_ids=("c1","c2"), top_id="c1"),
    AnswerObservation(member_id=1, evaluated_ids=("c1","c3"), acceptable_ids=("c1","c3"), top_id="c1"),
]
rel2 = fit_relative_choice(ans2)
st = rel2.member_state(1)
print(f"\n  三条回答都选 c1 为顶项后：v(c1)={dict(zip(st.candidate_ids, st.means))['c1']:.3f}, "
      f"v(c2)={dict(zip(st.candidate_ids, st.means))['c2']:.3f}")
est2 = estimate_utilities(rebuild_acceptance(ans2, CATS), rel2, {}, UtilityConfig())
r2 = dict(zip(est2.candidate_ids, est2.mu[0]))
print(f"    mu: c1={r2['c1']:.4f} c2={r2['c2']:.4f} 差距={r2['c1']-r2['c2']:.4f}")
print("  -> 结论：σ_v=1 的强收缩下，顶项只能把 mu 推高百分之几，w_top 名义权重 1 实际几乎不起作用")

# ---------- 2. scale 是否真的有区分度 ----------
show("2. 提问尺度 s_mi 的分布（决定调度排序）")
scales = dict(zip(est2.candidate_ids, est2.scale[0]))
for c in ("c1","c2","c5","c6"):
    print(f"    {c}: scale={scales[c]:.4f}")
print(f"  未答项 scale 最大值/最小值比 = "
      f"{max(scales.values())/max(1e-9,min(v for v in scales.values() if v>0)):.2f}")
print("  -> 所有未答项的 scale 由同一个类别先验决定，几乎相同；调度里的 uncertainty 因子基本是常数")

# ---------- 3. λ 是否真的改变了结果 ----------
show("3. λ=0 vs 0.25 在随机实例上的结果差异")
from app.constraints.compiler import compile_constraints
from app.constraints.dsl import ConfirmedConstraint, ConstraintKind, ConstraintScope, Candidate, Member
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.mean_fair import SolverConfig
from fractions import Fraction

def make_model(rng, d=4, n=13, k=3, budget_ratio=0.6):
    cands = [Candidate(candidate_id=f"i{j:02d}", cost_cents=rng.randrange(1000, 6000),
                       category="X" if j % 2 else "Y", active=True) for j in range(n)]
    members = [Member(member_id=m, active=True) for m in range(d)]
    costs = sorted(c.cost_cents for c in cands)[:k]
    budget = int(sum(costs) * (1 + budget_ratio))
    cons = [ConfirmedConstraint(constraint_id="b", kind=ConstraintKind.BUDGET, scope=ConstraintScope.PUBLIC, amount_cents=budget),
            ConfirmedConstraint(constraint_id="k", kind=ConstraintKind.EXACT_COUNT, scope=ConstraintScope.PUBLIC, min_count=k)]
    return compile_constraints(cons, cands, members), cands, members

diff = 0
tot = 0
for seed in range(60):
    rng = random.Random(seed)
    model, cands, members = make_model(rng)
    mu = [[rng.choice([0.0, 0.3, 0.9, 1.4, 1.9]) for _ in model.candidate_ids] for _ in model.member_ids]
    r0 = solve_mean_fair(mu, model, SolverConfig(lam=Fraction(0), time_limit_seconds=5))
    r25 = solve_mean_fair(mu, model, SolverConfig(lam=Fraction(1, 4), time_limit_seconds=5))
    tot += 1
    if r0.candidate_ids != r25.candidate_ids:
        diff += 1
print(f"  60 个随机实例中 λ=0 与 λ=.25 结果不同：{diff}/{tot} = {diff/tot:.0%}")
print("  -> 公平项确实改变了推荐，不是装饰")

# ---------- 4. 选择性低报的实际收益 ----------
show("4. 选择性低报（只报最想要的一项）能把推荐拉向自己多少")
# 构造：3 人、6 候选、k=2，A 最想要 i0
model, cands, members = make_model(random.Random(7), d=3, n=6, k=2)
cids = model.candidate_ids
print("  候选：", cids)
honest = [
    [1.9, 1.7, 1.6, 1.5, 0.0, 0.0],   # A 诚实
    [1.2, 1.3, 1.1, 1.0, 1.4, 0.9],   # B
    [1.1, 1.0, 1.3, 0.8, 1.2, 1.4],   # C
]
strategic = [honest[0][:], honest[1][:], honest[2][:]]
strategic[0] = [1.9, 0.0, 0.0, 0.0, 0.0, 0.0]  # A 只承认 i00 可接受
for name, mu in (("诚实", honest), ("A 低报", strategic)):
    r = solve_mean_fair(mu, model, SolverConfig(lam=Fraction(1,4), time_limit_seconds=5))
    u = [sum(row[cids.index(c)] for c in r.candidate_ids)/2 for row in honest]
    print(f"    {name:8s} -> {r.candidate_ids}  A 真实效用={u[0]:.2f} 均值={sum(u)/3:.2f} 最差={min(u):.2f}")

# ---------- 5. 保守区间在“全未知”状态下有多宽 ----------
show("5. epsilon_safe 在无任何作答时的取值（理论上限）")
from app.diagnostics.safe_bound import compute_safe_bound
model, cands, members = make_model(random.Random(11), d=4, n=13, k=3)
bnd = build_bounds([], {}, candidate_ids=model.candidate_ids, member_ids=model.member_ids)
inc = solve_mean_fair([[0.75]*13 for _ in range(4)], model, SolverConfig(lam=Fraction(1,4), time_limit_seconds=5))
rep = compute_safe_bound(bnd, inc.candidate_ids, model, SolverConfig(lam=Fraction(1,4)))
print(f"  epsilon_safe={rep.epsilon_safe:.3f}  UB_h={rep.upper_bound_h:.3f}  "
      f"H_l(incumbent)={rep.lower_bound_incumbent:.3f}  coverage={rep.coverage_ratio:.3f}")
print("  -> 无作答时界恒为 1，『风险仪表』在演示中读数无法区分好坏策略")
