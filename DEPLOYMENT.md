# 可复现运行与部署

所有命令从仓库根目录执行，路径不含盘符、用户名或预先存在的虚拟环境。配置文件按源码位置定位：先读取根目录 `.env`，再读取 `backend/.env`，显式环境变量优先。请勿同时维护互相冲突的配置文件。

## 固定运行环境

使用 Python **3.12.14**、Node **24.16.0**、npm **11.x**。Python 依赖由 `backend/requirements.lock.txt` 精确锁定（含 Linux 的 uvloop 平台标记），前端使用 `package-lock.json` 和 `npm ci`；不要用宽泛的 `requirements.in` 安装运行环境。`requirements.txt` 也委托给锁文件。升级依赖时同时更新锁文件并运行 CI。

容器采用 Python 3.12.14、Node 24.16.0、MySQL 8.0.45、Redis 7.4.2、nginx 1.28.0 的明确版本标签。Docker Engine 与 Compose v2 是容器部署的外部前置条件，建议 Compose 2.24+。标签并非镜像摘要；需要镜像字节级一致时，将验证过的镜像摘要保存到私有镜像仓库再固定 `@sha256`。

## 推荐：完整容器部署

```sh
python scripts/configure.py
docker compose --env-file .env -f deploy/compose.yaml config --quiet
docker compose --env-file .env -f deploy/compose.yaml up --build -d
docker compose --env-file .env -f deploy/compose.yaml ps
```

`configure.py` 用系统安全随机源生成独立数据库密码和邀请签名密钥，拒绝覆盖已有配置。保留并备份 `.env`，重启不要重新生成：修改邀请密钥会使旧邀请链接失效，修改数据库密码不会自动修改已有数据卷内的账号。

编排依次等待 MySQL 就绪、执行 `alembic upgrade head`，再启动 API、outbox worker 和前端。访问 `http://localhost:8080`；局域网使用部署机器的地址和同一端口。`/api` 同源代理到后端；SPA 深链接会回退到首页。MySQL 和 Redis 不发布宿主机端口。API 诊断端口只绑定本机 `127.0.0.1:8000`。公网部署在入口配置 HTTPS。

编排强制 `APP_ENV=prod`，默认邀请密钥或少于 32 字符的密钥会导致启动失败。配置缺失会在 Compose 校验阶段失败。`WEB_PORT` 可更改。上述命令不需要本机 MySQL、Redis、Node 或预先存在的虚拟环境，生成配置时只需标准 Python。

## 本地开发

安装上述 Python/Node 版本，并自行启动 MySQL/Redis，或使用现有开发服务。先生成 `.env`，将 `DATABASE_URL`、`REDIS_URL` 改为实际服务地址；连接密码须进行 URL 编码，生成脚本默认产生的十六进制密码无需编码。创建全新虚拟环境，不复制别人的 `.venv`：

```sh
python -m venv .venv
```

Windows PowerShell 激活：`.venv\Scripts\Activate.ps1`；macOS/Linux：`source .venv/bin/activate`。随后使用激活后的 `python`：

```sh
python -m pip install -r backend/requirements.lock.txt
cd backend
python -m alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

另开终端，激活同一环境并进入 `backend`，运行 `python -m app.workers.outbox_projection --loop`。再开终端进入 `frontend`：

```sh
npm ci
npm run dev -- --host 0.0.0.0
```

开发前端默认代理 `127.0.0.1:8000`；需要跨容器或远程后端时设置 `VITE_PROXY_TARGET`（仅开发代理）。生产前端默认请求同源 `/api`，仅外部 API 托管需要构建时设置 `VITE_API_BASE`；前端变量均会进入浏览器，不能放服务密钥。

SQLite 可用于本地演示和单元测试，不能代替 MySQL 的多 worker `SKIP LOCKED` 并发验证。切换现有数据库前先备份，然后运行迁移，新增 `outbox.next_retry_at` 和重试索引。

## 可选 MaaS 和识别鉴权

MaaS 不是核心流程的必需依赖。默认关闭文本 LLM，菜单可通过 CSV/JSON/文本导入。启用 OCR 时配置 `MAAS_API_KEY`、`MAAS_BASE_URL`、`MAAS_VISION_MODEL`。创建页还没有本协商成员身份，可使用此前有效的成员身份，或由部署方设置至少 32 字符的独立 `MENU_OCR_ACCESS_TOKEN`，私下交给使用者在识别凭证框输入。凭证不写入前端构建、不在浏览器持久化。

识别通过 Redis 原子窗口限流，每个身份默认每分钟 5 次，所有 API/worker 共享的全局上限默认每分钟 20 次（`MENU_OCR_GLOBAL_REQUESTS_PER_MINUTE`），防止通过创建新成员绕过单身份配额；Redis 不可用返回 503。每个 API 进程最多 4 个识别调用（`MENU_OCR_MAX_CONCURRENT`），超出时立即返回 429，避免占满业务线程。`MENU_IMAGE_MAX_BYTES` 默认 6 MiB，网关限制请求体 9 MiB；调整图片上限时同步调整 `frontend/nginx.conf`。请求体、编码长度和解码后大小均有限制。`MENU_OCR_TIMEOUT_SECONDS=10` 限制上游 HTTP 各阶段，浏览器整体请求 15 秒超时；超时后可改用文本导入。

## 超时、池与恢复

`.env.example` 声明了可调整配置。数据库连接池默认为每 API/worker 进程 5 个常驻连接、最多额外 10 个，等池 5 秒、连接 5 秒。Redis 数据请求连接/读写为 2/5 秒；Redis 健康探针独立为 0.5 秒。`/healthz` 在任一依赖异常时返回 503。

整轮计算的默认预算为 10 秒：主求解、挑战池、保守界共享调用方注入的单调时钟截止，子求解按剩余预算缩短。预算用尽时保留可行结果、标记未完成的挑战池并使用保守上界；未得到可行解时返回重试。Python/数据库和网络调度开销不是严格实时保证。默认单搜索线程、固定种子、显式破同分规则；求解证优完成时结果可重复，超时场景在不同硬件上可能返回不同可行解，状态会如实区分。

进程在 `CLOSING` 或 `CHECKING_FEASIBILITY` 冻结输入提交后崩溃，可以使用相同封轮接口重试：`POST /negotiations/{id}/rounds/{round_no}/close`，携带当前有效成员 Bearer 令牌。发起者可在对话/看板或设置页点击重新计算；不需要人工改业务状态。重复计算通过原有版本 CAS 防止重复提交。成员退出先持久化失效与新成员版本，再锁外计算；计算失败后退出本身仍有效。

outbox 默认退避 5、10、20、40、80、160、300 秒，到第 8 次失败进入死信。主循环发生数据库异常后继续重试；新事件和未到期的失败事件分开调度。需要重放死信时先修依赖，再在受控运维事务中把相应行的 `status` 设为 `pending`、`attempts` 设为 0、`next_retry_at` 和 `last_error` 设为 NULL；版本门控保证 Redis 重放幂等。重建缓存：在 `backend` 中运行 `python -m app.workers.outbox_projection --rebuild <id>`。

## 验证

在仓库根目录、激活环境后：

```sh
python -m pip check
python -m pytest -q
python backend/scripts/contract_snapshot.py --check
cd frontend
npm ci
npm test
npm run build
```

CI 在 Windows/Linux 上运行同一流程。OpenAPI 快照及前后端枚举对比会阻止接口悄悄漂移；有意修改契约时同步修改 `frontend/src/types.ts`，再运行 `python backend/scripts/contract_snapshot.py`，审查快照差异。

本次本机验证不包含真实 MySQL 多 worker 抢锁、真实 Redis Lua、Linux 容器启动或公网部署；这些需要实际 Docker 引擎/外部服务执行。不要把 SQLite/FakeRedis 的测试结果当成云端验收。
