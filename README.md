# 私密协商器（和议）

3–6 人小组在预算与数量约束下，各自私密表达偏好，由系统逐轮补问、收窄并求出全员可接受的组合方案。典型场景是聚餐选菜，同一机制可复用于活动、出游等群体决策。

- 后端：FastAPI + SQLAlchemy/Alembic + MySQL + Redis + OR-Tools CP-SAT
- 前端：Vue 3 + TypeScript + Vite
- LLM（可选）：华为云 MaaS，用于底线解析、问题措辞、顾虑整理；关闭后核心协商完整可跑

## 解决什么问题

群体决策的难点不是"没有方案"，而是"没人愿意先说出自己的真实偏好"：

- 公开讨论会让强势成员主导，沉默成员被代表；
- 直接投票只能表达"要/不要"，无法表达预算、数量、整组禁忌等硬约束；
- 一旦有人提出顾虑，往往以"算了"收场，缺少可审计的处理路径。

本项目的做法是把偏好收集与方案求解分离：成员只在本人视角作答，系统在服务端完成约束编译与组合求解，公共视野只看到候选、推荐与整体阶段。

成员治理和强制移除授权已实现，规则、接口边界与部署迁移见 [MEMBER_LIFECYCLE.md](MEMBER_LIFECYCLE.md)。

## 核心流程

1. **发起**：创建协商，导入候选清单（单次 13–30 项）、预算与数量约束、整组禁选项。
2. **私密作答**：成员通过邀请链接独立作答，可标记"能接受/最想要"，也可声明两类底线（整组避开、本人不参与并给出替代要求）。
3. **组合求解**：把硬约束编译为 CP-SAT 模型，在 `mean_fair` 目标（平均效用与最差成员效用的加权）下求解可行组合。
4. **补问收窄**：挑战池按信息增益挑选下一批问题，逐轮缩小不确定性；连续两轮推荐组合不变即视为稳定。
5. **私下确认**：推荐方案下发到每位成员本人，全员确认后才结束；任一成员拒绝则回到补问。
6. **顾虑闭环**：成员可私下提交顾虑，系统明确告知保密例外与转交范围，由发起者决定继续或停止。

## 设计原则

- **硬约束不可妥协**：预算、数量、整组禁选、个人底线、替代要求在推荐组合中零违规，不做"先求解后过滤"。
- **私密数据不出本人边界**：个人答案、理由、效用与调度分值不进入公共 DTO、普通日志或 LLM 输入。
- **未知不等于无解**：只有求解器枚举不到可行组合才返回无解，未计算或异常不得冒充无解。
- **算法模块无副作用**：约束、偏好、求解、调度、诊断只接收显式输入与种子，不读数据库、缓存、系统时间，不调用 LLM。
- **RDS 为权威事实，Redis 为投影**：先落库再投影，缓存失效可重建，历史不丢。
- **服务端校验权限**：前端不作为信任边界，邀请凭证与成员身份均在服务端校验。

## 目录结构

```text
backend/
  app/
    api/           HTTP 接口（协商、轮次、本人视图、顾虑、菜单、健康检查）
    engine/        状态机、封轮、确认、事件、投影
    constraints/   约束 DSL、编译与可行性预检
    preference/    接受预测、顶项模型、保守区间
    solver/        CP-SAT 公平求解、挑战池、DP 基线
    scheduler/     提问调度与覆盖
    diagnostics/   保守界与风险诊断
    llm/           MaaS 三入口（可整体关闭）
    workers/       outbox → Redis 投影 worker
    simulation/    模拟实验：生成器、作答者、指标、网格
    tests/         pytest 测试（207 项）
  alembic/         数据库迁移
  Dockerfile
frontend/
  src/
    pages/         创建、加入、作答、底线、顾虑、确认、结果、历史等页面
    components/    候选卡片、消息气泡、二维码等
    chat/          聊天式协商的派生逻辑
deploy/
  compose.yaml     API + MySQL + Redis 编排
experiments/       固定种子实验的原始结果与复现脚本
```

## 快速开始

### 1. 环境

- Python 3.11+（镜像使用 3.12）
- Node 18+
- MySQL 8 与 Redis 7（本地开发也可用 SQLite 作为临时数据库）

### 2. 后端

```bash
cd backend
python -m venv ../.venv
../.venv/Scripts/pip install -r requirements.lock.txt   # Windows
# source ../.venv/bin/activate && pip install -r requirements.lock.txt   # macOS/Linux

cp ../.env.example ../.env    # 按需修改 DATABASE_URL / REDIS_URL
../.venv/Scripts/alembic upgrade head
../.venv/Scripts/uvicorn app.main:app --reload --port 8000
```

服务启动后可访问 `http://127.0.0.1:8000/healthz` 与 `http://127.0.0.1:8000/docs`（非 prod 环境）。

### 3. 前端

```bash
cd frontend
npm install
npm run dev     # http://127.0.0.1:5173，/api 自动代理到 :8000
```

### 4. 容器编排

```bash
cd deploy
docker compose up --build
```

## 测试

```bash
cd d:/learning/ict/privnegotiate
.venv/Scripts/python.exe -m pytest backend/app/tests -q
```

当前基线：207 项全部通过，覆盖约束编译、n≤12 全枚举对照、偏好数值检查、权限越权、幂等与失败路径，以及公共进度接口的访问边界（`test_public_access.py`）。

## 模拟实验

实验使用固定种子，结果与复现脚本归档在 `experiments/`：

```bash
cd backend
# 消融与配对可操纵性
../.venv/Scripts/python.exe -m app.simulation.grid --seeds 1001-1050 \
  --strategies heuristic --policies honest \
  --scenarios size_d3_n13,size_d4_n20,size_d6_n30,cond_minority,cond_heterogeneous,cond_skip_30 \
  --ablations no_category_sharing,no_top_likelihood,flat_top,fixed_unknown,no_personal_challenger,no_interval_challenger,no_exploration,no_load \
  --manipulation --workers 12 --output ../experiments/ablation-2026-10-26.json

# λ 曲线
../.venv/Scripts/python.exe -m app.simulation.grid --seeds 1001-1050 \
  --strategies heuristic --policies honest \
  --scenarios size_d3_n13,size_d4_n20,size_d6_n30,cond_minority,cond_heterogeneous,cond_skip_30,cond_tight_budget,cond_k3 \
  --ablations lam0,lam5,lam75 --manipulation --workers 10 \
  --output ../experiments/lambda-curve-2026-10-26.json
```

## 当前状态与限制

已完成：约束编译与 CP-SAT 求解、偏好估计、挑战池提问、封轮与确认闭环、顾虑流程、outbox 投影、MaaS 三入口降级、菜单导入与识别、前端全流程、公共进度接口的成员令牌鉴权。

公共进度仅本协商成员可见：`GET /negotiations/{id}/public` 必须携带该协商成员的 Bearer 令牌；无凭证、邀请凭证、无效/停用令牌和跨协商令牌均返回 401。该接口只返回公共字段白名单，不包含他人的回答、理由、底线来源或个人效用。邀请页加入前不预览协商内容；已有成员身份直接复用。

尚未完成，材料中不得声称已完成：

- 华为云 RDS/Redis 开通与公网部署；
- 演示模式与真实数据的隔离；
- 真实 Redis Lua 门控的云上验证；
- 真人 A/B 试用（模拟实验的真值与估计模型同族，只能验证求解器与管线，不能证明模型适合真人）；
- 开源所需的 LICENSE 与部署说明。

## 文档

- [`HANDOVER.md`](HANDOVER.md)：赛题约束、算法规格、契约、批次与红线
- [`HANDOVER-2026-10-25-全量改动汇总.md`](HANDOVER-2026-10-25-全量改动汇总.md)：2026-10-25 全量改动与验证记录
- `../ict2026/项目文档/`：技术设计（唯一真源）、分工与协作、参赛总体规划（不在本仓库）

## 许可证

尚未选定。开源发布前需补齐 LICENSE 文件。
