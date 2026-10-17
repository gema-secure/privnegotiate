# 私密协商器 · AI 执行交接文档

- 版本：v1.0（2026-10-17）
- 读者：接手的 AI 编码代理（下称“你”）
- 目标：2026-11-15 前交付可运行的 MVP、可录制的 Demo、初赛材料骨架
- 本文件自包含；需要细节时读 §12 列出的两份文档
- 赛事：第十一届华为 ICT 大赛 · 创新赛 · **赛题一**（基于华为云产品打造 AI 创新应用）

---

## 0. 十分钟上手

1. 读三个文件（按顺序）：
   - 本文件；
   - `../ict2026/技术设计-私密协商器-代码实现与算法方案-最终整合稿.md` 的 §4–§9（偏好、约束、求解、提问、诊断、状态机）；
   - `../ict2026/参赛总体规划-2026-10-17.md` 的 §2（赛道一合规清单）与 §7（里程碑）。
2. 确认环境：Windows、git、Python 3.11、Node。**本机没有 Docker**；容器验证放云主机，或先装 Docker Desktop。
3. 第一轮只产出：文件树 + §6 五份契约的类型定义草案。**不要写业务代码。**

---

## 1. 一句话与成功标准

3–6 人小组，各自私密勾选“能接受 / 最想要”，系统在预算与数量约束下算出一套组合，逐轮补问，两轮稳定后私下确认，全员确认才算结束。

成功标准，按优先级：

1. **硬约束零违规**：预算、数量、整组禁选、个人底线、替代要求，任何一条都不能被推荐组合违反。
2. **云端可访问**：公网打开，能完整走一轮协商（发起 → 作答 → 推荐 → 确认）。
3. **指标可复现**：固定 seed 的模拟实验脚本 + 原始结果，任何人能重跑。
4. **不劣于基线**：同提问预算下，最差成员效用不低于“固定评分 + 可加求解 + 对称差”基线。

---

## 2. 赛事约束（不可违背）

| # | 要求 | 含义 | 验收证据 |
|---|---|---|---|
| 1 | 使用华为云码道 CodeArts 代码智能体 | 主代码由码道生成/修复/部署 | 码道会话、生成 diff、录屏 |
| 2 | 用已有智能体 + Skills 关联开通 RDS、Redis | 不能只手工点控制台 | 技能调用记录或生成的开通命令 + 实例截图 |
| 3 | 作品部署在华为云、可公网访问 | 不能只本地跑 | 公网 URL、部署录屏 |
| 4 | 提交技术文档、代码、演示视频 | 三件套齐全 | 材料清单逐项对照 |
| 5 | 入围总决赛后开源至 Gitee/GitHub | 初赛后做 | 仓库链接 + LICENSE |
| 6 | 3 学生 + 1 指导老师、同校、报名后不换人 | 已组队 | 报名截图 |

**评分导向**：官方表述是“以应用创新为主要考察目标”。算法深度是支撑，不是单独得分项。不要为了算法优雅牺牲可运行、可演示、可解释。

---

## 3. 当前状态

| 项 | 状态 |
|---|---|
| 设计 | 定稿（最终整合稿 719 行） |
| 代码 | 0 行；仓库已初始化，含 `README.md`、`prompts/P1-01-骨架.md` |
| 云资源 | 未开通 |
| 码道 | VS Code 插件已安装，未开始生成 |
| 本机 | git ✅、node ✅、python ✅、docker ❌ |

---

## 4. 架构与目录

```
backend/app/
  api/              negotiations.py members.py answers.py concerns.py demo.py
  core/             config.py auth.py permissions.py
  models/           business.py events.py outbox.py
  schemas/          commands.py public_views.py private_views.py
  engine/           state.py commands.py close_round.py replay.py
  constraints/      dsl.py compiler.py validation.py
  preference/       acceptance.py relative_choice.py estimates.py bounds.py
  solver/           mean_fair.py cpsat_adapter.py dp_baseline.py challengers.py
  scheduler/        heuristic.py coverage.py
  diagnostics/      safe_bound.py conflict.py counterfactual.py
  llm/              maas_client.py parse_constraints.py wording.py concerns.py
  workers/          round_jobs.py outbox_projection.py
  simulation/       generators.py responders.py baselines.py metrics.py
  tests/
frontend/src/       pages/ components/ api/ stores/
deploy/             compose.yaml env.example README.md
```

铁律：

- `constraints / preference / solver / scheduler / diagnostics` 是**纯函数**：只吃显式输入、配置、seed，不读数据库、不读系统时间、不调 LLM。
- `engine` 负责身份、权限、事务、轮次关闭、事件回放、发布。
- LLM 只有三个白名单位置：底线解析（须用户确认）、问题措辞（只给公开候选）、顾虑整理（保留原文）。LLM 不改分数、不删候选、不判无解。

---

## 5. 算法 v1（`mean_fair_v1`）

### 5.1 符号

- 成员 `m ∈ M`，`d=|M|∈[3,6]`；候选 `i ∈ C`，`n∈[13,30]`；选 `k` 个，`k∈[1,8]`。
- 费用 `c_i` 与预算 `B` 全部用**整数分**。
- `x_i ∈ {0,1}`，`X` 为硬约束可行域。

### 5.2 效用刻度

```
u_mi = e_mi · a_mi · [1 + w_top · sigmoid(v_mi)]
U_m(x) = Σ_i u_mi x_i / [k(1+w_top)]    ∈ [0,1]
```

- `e_mi`：本人是否参与该项（本人不参与 → 0）。
- `a_mi`：接受状态（已明确回答 → 取最新有效值；未答 → 用预测均值）。
- `v_mi`：集合内相对偏好强度。
- `w_top` 默认 1。固定分母 `k(1+w_top)`，**不按个人接受数或个人可达上限缩放**。

### 5.3 接受预测（Beta-Bernoulli，按成员 × 类别）

```
p_mc ~ Beta(κp0, κ(1-p0))，p0=0.5，κ=2
n_mc = 本人该类别有有效明确标签的不同候选数
s_mc = 其中接受项数
A = κp0 + s_mc ；B = κ(1-p0) + n_mc - s_mc
未评价项 P(a=1) = A/(A+B)
```

- 同一项重复提交、重放不增加 `n`；修改标签后从有效事件重算。
- 类别缺失 → `unknown` 类别，用更弱先验。
- **不跨成员**借用他人答案建先验。

### 5.4 顶项模型（Plackett-Luce，MAP）

```
P(j | A, v_m) = exp(v_mj) / Σ_{i∈A} exp(v_mi)
L(v) = ||v||²/(2σ_v²) + Σ_{有效回答, |A|≥2} [logsumexp(v_A) - v_j]
```

- `τ=1` 固定；`σ_v=1` 初值；`|A|≤1` 没有相对似然，不更新。
- 一次多项选择只产生一个顶项似然，**不拆成多个成对样本**。
- 用 Newton + 回溯线搜索；记录迭代数、梯度残差；不收敛 → 退回先验并标 `NOT_CONVERGED`。
- Laplace 协方差可后置；首版允许用固定 σ 代替。

### 5.5 保守区间（不依赖先验校准）

| 状态 | `[l_mi, h_mi]` |
|---|---|
| 未评价 / 跳过，且参与 | `[0, 1+w_top]` |
| 明确接受，且参与 | `[1, 1+w_top]` |
| 明确不接受 / 本人不参与 | `[0, 0]` |
| 整组禁选 | `x_i = 0` |

顶项噪声**不能**用来收紧硬区间。

### 5.6 公平求解（OR-Tools CP-SAT）

```
J_mean(x) = (1-λ)/d · Σ_m U_m(x) + λ · min_m U_m(x)
λ = 0.25；λ=0 为可加平均基线
```

整数化：`Q=1000`，`b_mi = floor(Q·mu_mi/(1+w_top))`，`T_m = Σ b_mi x_i`，`0 ≤ z ≤ T_m`，最大化：

```
(q-p) Σ_m T_m + p·d·z        （λ = p/q）
```

- 目标分母 `q·d·Q·k`；检查 int64 上界。
- 同分：先锁定主目标最优值 → 最小化费用 → 按候选 ID 字典序。
- 状态映射：`OPTIMAL` 才可说“量化模型精确最优”；`FEASIBLE` 发布并标 gap；`INFEASIBLE` 仅在求解器证明时进入无解流程；`UNKNOWN` **不得**说“无可行组合”。
- DP 只做 `λ=0`、可加、约束可正确编码时的基线/加速路径。

### 5.7 挑战池与提问

池 `K=16`：当前推荐、上一轮可行推荐、各成员个人预测最优（≤d 个）、保守上界最优、少量高 `J_mean` 且差异大的组合。

```
g_S = max(0, J(S_hat) - J(S))
d_i = max_{S≠S_hat} 1[i ∈ S △ S_hat] / (η + g_S)，本批归一化
r_m = U_m(S_hat)
f_m = clip((1-λ) + λ/(ε + r_m), 1/4, 4)
priority(m,i) = [d_i + ρ_explore] · s_mi · f_m / (1 + β·load_m)
```

首试 `η=.05、ε=.05、ρ_explore=.2、β=.5`。每人每轮 2–4 项、并集 ≤6；默认不重复已答项；跳过不更新偏好；只问“本人参与且未整组禁选”的候选。

### 5.8 风险界（定位：风险仪表，不是证书）

```
ε_safe = min(1, max(0, UB_h - H_l(S_hat)))
```

`H_h` 用上界、`H_l` 用下界，同一 λ 与归一化；`UB_h` 必须覆盖整个 `X`。覆盖率低时该值会长期偏宽，这是正确行为，**不参与停止判定**，只做诊断与确认页提示。

### 5.9 首版明确不做

- 场景公平（`E[min]`）、KG 全量计算、粒子 MCMC、属性级特征工程；
- 重复已答项来压 `ε_safe`；
- 任意反事实差值对用户开放（仅内部调试）；
- 任何“领域首次 / 全局最优”的表述。

---

## 6. 五份契约（先冻结，再开工）

### 6.1 `SolveResult`

```
status: OPTIMAL | FEASIBLE | INFEASIBLE | UNKNOWN | ERROR
candidate_ids: list[str]
total_cost_cents: int
objective_quantized: int
objective_float: float
best_bound: float | null
absolute_gap: float | null
quantization_bound: float
tie_break_complete: bool
wall_time_ms: int
config_version: str
```

### 6.2 `UtilityEstimate` / `UtilityBounds`

```
UtilityEstimate:
  mu:  {member_id: {candidate_id: float}}   # [0, 1+w_top]
  s:   {member_id: {candidate_id: float}}   # [0,1]，仅排序用
  acceptance_p: {member_id: {category: float}}
  relative_v:   {member_id: {candidate_id: float}}
  fit_status:   {member_id: OK | PRIOR_FALLBACK | NOT_CONVERGED}
  config_version: str

UtilityBounds:
  low:  {member_id: {candidate_id: float}}
  high: {member_id: {candidate_id: float}}
```

### 6.3 `ChallengePool` / `QuestionPlan`

```
ChallengePool:
  combos: [{combo_id, items: [candidate_id], j_mean: float,
            source: CURRENT|PREVIOUS|PERSONAL|UPPER|DIVERSE}]
  complete: bool            # false = 池不完整，不得称 Pareto 前沿

QuestionPlan:
  per_member: {member_id: {candidate_ids: [str], scores: [float], reasons: [str]}}
  batch_size: int           # ≤6
  generated_from: round_id
```

### 6.4 事件与状态

事件类型：`negotiation.created`、`candidate.added`、`member.joined`、`constraint.confirmed`、`question.batch_issued`、`answer.submitted`、`round.closed`、`result.computed`、`result.confirmed`、`concern.submitted`、`initiator.decided`。

状态机：

```
CREATED → COLLECTING_BOTTOMLINE → CHECKING_FEASIBILITY
  ├─ INFEASIBLE → ADJUSTING → CHECKING_FEASIBILITY
  ├─ UNKNOWN/错误且无合法组合 → RETRY_OR_REVIEW
  └─ 已有合法组合 → ASKING → ANSWERING → CLOSING → COMPUTING → UPDATING
       ├─ 未稳定且未到上限且有新题 → ASKING
       ├─ 连续两轮同推荐 → PRIVATE_CONFIRM
       └─ 达上限/无可补问题 → BEST_EFFORT
```

稳定性：只比较**已封轮且计算结果已提交**的两轮；纯跳过、同输入重算不增加 `stable_count`；约束、成员、k/B、λ、模型版本变化 → 重置。

### 6.5 DTO 白名单

- 公共：候选、推荐、总费用、公共条件、阶段。
- 本人：本人题目/回答/选择链/确认。
- 发起者：公共内容 + 已告知转交的顾虑；**不含**他人回答、理由、个人链。
- 禁止：把内部模型对象、个人效用、冲突核来源直接序列化给前端或写日志。

---

## 7. 里程碑与验收

| 批次 | 内容 | 完成定义（DoD） |
|---|---|---|
| P0 | 冻结输入、状态、DTO、配置、依赖版本 | 五份契约评审通过；`.env.example` 无密钥 |
| P1 | 骨架、RDS/Redis、事件/outbox、部署空壳 | `/healthz` 三 ok；**云上公网可访问**；Skills 开通有录屏 |
| P2 | 约束 DSL + CP-SAT 均值公平 + DP 基线 | n≤12 全枚举 1000 seed 一致；UNKNOWN ≠ INFEASIBLE |
| P3 | Beta 接受 + PL MAP + 均值/尺度/区间 | 唯一计数；单可接受项无额外证据；跳过不否定；失败路径有状态 |
| P4 | API、前端两页、封轮、确认/顾虑闭环 | 同轮多成员提交成功；确认版本失效正确；LLM 关闭仍可跑完 |
| P5 | 挑战池、提问启发式、风险界、实验脚本 | 第三挑战者覆盖；界不被违反；消融可一键重跑 |
| P6 | MaaS 三入口、隔离 Demo、云部署 | 真实模式无跨成员读取；Demo A→B→C 可录 |
| P7 | 材料、许可证、README、录屏 | 每个宣称都有对应证据，未完成项明确标注 |

时间：P0–P1 到 10.24；P2–P4 到 11.15；P5–P6 到 11.20；材料与报名 11 月中。**报名截止 11-30，不要压线。**

---

## 8. 与码道的协作协议

1. **一次会话只做一个批次**，不要把 P1–P4 塞进一个提示词。
2. 每次先要“文件树 + 计划”，确认后再生成；生成后立刻 `git add -A && git commit`，保留 diff。
3. 报错处理：贴完整报错 + 相关文件 + 期望行为，要求先给根因再改；一次只改一个问题。
4. 所有会话保留历史（码道自带历史会话），截图归档到 `开发证据/`，命名 `日期_环节_内容`。
5. 主代码只用码道生成；其他 AI 工具只做 review、文档、测试思路，**不生成交付代码**。

---

## 9. 红线

- 不改产品语义：两类底线、跳过 ≠ 不接受、不代替成员确认。
- 不跳过硬约束，不做“先求解后过滤”。
- `UNKNOWN` 不得表述为无解；`FEASIBLE` 不得表述为最优。
- 未知字段用 `null` / `unknown`，**不用 0 冒充**。
- 不伪造指标、截图、运行结果；未跑就说未跑。
- AK/SK、连接串不进仓库、不进日志、不进对话。
- 个人答案、理由、效用不进公共 DTO、不进普通日志、不进 LLM 输入。

---

## 10. 证据清单（边做边收）

| 证据 | 时点 |
|---|---|
| 码道会话（提示词 + 生成结果） | 每批 |
| 生成前后 `git diff` / commit | 每批 |
| Skills 开通 RDS/Redis（实例 ID、区域） | P1 |
| 云上 `/healthz` + 公网 URL | P1 |
| 部署录屏 3–5 分钟 | P1 / P6 |
| 全枚举测试报告（1000 seed） | P2 |
| 模拟实验原始结果 + 脚本 | P5 |
| Demo A→B→C 录屏 | P6 |
| 材料三件套 | P7 |

---

## 11. 风险与兜底

| 风险 | 兜底 |
|---|---|
| OR-Tools 在目标镜像不可用 | 立刻回报；退回 DP + `λ=0`，材料改口径 |
| PL 在 3–4 条观测下不收敛 | 退回先验，标 `NOT_CONVERGED`/`PRIOR_FALLBACK`，不阻塞流程 |
| 码道没有云资源技能 | 让它生成 KooCLI/Terraform 命令，终端执行并录屏；材料如实写 |
| 本机无 Docker | 云主机装 Docker，或本地装 Docker Desktop |
| 区域不一致（RDS 与 ECS） | 全部选同一 region；已开错就重建 |
| 时间不够 | 砍 P8、诊断 UI、Laplace；保 P1–P4 与硬约束 |

---

## 12. 文档索引

- `../ict2026/技术设计-私密协商器-代码实现与算法方案-最终整合稿.md`：算法与工程细节（§4 偏好、§5 约束、§6 求解、§7 提问、§8 诊断、§9 状态机、§13 高级版）。
- `../ict2026/参赛总体规划-2026-10-17.md`：赛道一合规清单、分工、里程碑、风险。
- `prompts/P1-01-骨架.md`：第一条可直接粘贴的码道提示词。
- `README.md`：仓库说明。

**现在开始**：输出文件树 + §6 五份契约的 Pydantic 草案，等我确认后再进入 P1。
