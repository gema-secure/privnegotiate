# 2026-11-03 改进与验证记录

按约定先处理 #4 → #8/#9 → #3 → #1 → #2，再完成其余项目。数据库锁顺序、结果版本 CAS、请求幂等、公共字段白名单和 Redis 投影版本门仍由原有测试覆盖。

| 编号 | 已落地行为 | 主要位置 |
|---|---|---|
| 1 | `CLOSING` 可重入，重用冻结截止和任务版本；重复提交由 CAS 拒绝。开轮中间状态也可通过封轮接口恢复 | `engine/closing.py`、`api/rounds.py` |
| 2 | 成员变更、开轮均先提交输入与任务版本，再锁外求解，最后锁内校验版本并提交 | `engine/membership.py`、`engine/feasibility.py` |
| 3 | outbox 增加 `SKIP LOCKED`、整批事务与分组 savepoint、SQL 原子计数、指数退避、循环异常恢复；MySQL 更新顺序显式固定 | `workers/outbox_projection.py`、迁移 `e8f301ab4210` |
| 4 | prod 遇默认密钥或不足 32 字符直接拒绝启动 | `core/config.py` |
| 5 | 业务错误记录代码/状态；未知异常返回结构化 500 和关联 ID，日志不输出异常参数或回答 | `main.py` |
| 6 | 主求解、挑战池、保守界共用整轮预算；调用方注入单调时钟截止，纯计算入口可使用显式计算预算；统一 solver 配置构造 | `solver/budget.py`、`engine/closing.py`、`engine/scheduling.py` |
| 7 | OCR 要求有效成员或独立识别凭证，Redis 原子身份/全局限流，原始请求体与编码长度提前限制；上游默认 10 秒、每进程并发 4 | `api/menu.py`、`core/request_limits.py`、`services/menu_ocr.py` |
| 8 | 全站共用请求 ID 实现，兼容局域网 HTTP，优先 Web Crypto | `frontend/src/requestId.ts` |
| 9 | 聊天未读只在最后一条消息 ID 变化时触发 | `frontend/src/pages/ChatPage.vue` |
| 10 | 请求与响应体整体 15 秒超时、轮询指数退避、失效 token 清理与重新加入提示；退出后的保留权限凭证继续有效 | `frontend/src/api.ts`、`polling.ts`、`session.ts` |
| 11 | stop 仅考虑当前结果、当前规则版本、当前参与成员的拒绝 | `engine/confirmation.py` |
| 12 | 开轮无成员/底线未齐的早退也产生事件和 outbox | `engine/feasibility.py` |
| 13 | 依赖异常 healthz 返回 503；显式数据库池/连接超时，Redis 数据面单例与独立探针超时 | `api/health.py`、`db/session.py`、`cache/redis.py` |
| 14 | 本地存储失败时身份保留内存，显示提示和重试保存；成功创建不会被存储异常伪装为失败 | `frontend/src/session.ts`、`App.vue` |
| 15 | η/ε 为零时分母有下限；求积节点强制 3–64，缓存键空间随之有界 | `scheduler/heuristic.py`、`preference/estimates.py` |
| 16 | 混合有效/无效菜单可明确确认仅导入有效项；沿用上轮失败在步骤 1 显示 | `frontend/src/pages/CreatePage.vue` |
| 17 | 9 个页面复用状态 composable，集中身份错误、旧响应保护与轮询；切换协商强制重新挂载页面 | `frontend/src/useNegotiationState.ts` |
| 18 | 同一挑战池的个人/上界方向克隆共享基础模型，仅替换目标；多样性方向保留独立约束模型 | `solver/challengers.py`、`solver/cpsat_adapter.py` |
| 19 | OpenAPI 审阅快照、前后端枚举对比、Windows/Linux CI 阻止无意契约漂移 | `tests/test_frontend_contract.py`、`backend/scripts/contract_snapshot.py` |
| 20 | 无新增回答时复用最终估计；显式证据用 SQL 筛选；`var_p` 改为派生属性；保守界不再覆盖决策配置版本；修正 upper 子求解耗时重复计数 | `simulation/runner.py`、`preference/acceptance.py`、`diagnostics/safe_bound.py` |

两处按实际代码保留了必要语义：模拟循环可能在最后一次估计之后追加回答，因此只有回答集合未变化时才复用估计；`var_p` 有现有契约测试，所以保留属性访问、消除冗余存储。

OCR 在创建协商前使用，不能要求尚不存在的“本协商成员”。创建页使用已有成员凭证或部署方提供的独立识别凭证；全局调用上限避免通过反复创建新身份绕过单身份配额。未配置 MaaS 时仍可完整使用文本导入和核心协商。

成员退出语义调整：退出事实和输入失效先原子提交，计算失败不撤销退出，而是保留可恢复任务。对应测试已从“全部回滚”改为“退出有效且封轮可恢复”。

复现方面：锁定 Python/Node 和依赖版本，补齐原锁文件缺失的 vue-tsc，统一 npm 官方源；提供不覆盖现有密钥的配置生成器、数据库迁移、完整容器编排、前端同源代理与 SPA 回退。详细操作见 [DEPLOYMENT.md](DEPLOYMENT.md)。`.env`、虚拟环境和验证临时文件不进入版本控制。

验证记录：后端 223 项测试通过；前端 6 项故障恢复/竞态测试通过；干净的 `npm ci` 安装、TypeScript 检查与 Vite 生产构建通过；OpenAPI 快照检查、Compose 配置校验和 Python 依赖一致性检查通过。测试覆盖封轮重入、重复提交、锁外计算、开轮恢复、输入版本变化、成员变更竞争、历史拒绝过滤、OCR 鉴权/体积/限流、整轮预算耗尽和 worker 数据库异常恢复。

本机 Docker 引擎未运行，实际容器启动、真实 MySQL 多 worker 并发和真实 Redis Lua 尚未实测；Linux CI 已配置但本次未在远端触发。SQLite/FakeRedis 测试不能替代这些部署验收。求解超时时不同硬件可能给出不同可行解，固定种子与证优后的确定性破同分规则也不能消除墙钟预算带来的差异。
