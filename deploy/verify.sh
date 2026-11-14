#!/usr/bin/env bash
# 部署验收：健康检查 + 业务级验收（创建一次协商必须返回 201）。
# 用法：bash deploy/verify.sh http://127.0.0.1        # 本机
#       bash deploy/verify.sh http://<ECS 公网 IP>    # 云上
set -euo pipefail

BASE="${1:-http://127.0.0.1}"

python3 - "$BASE" <<'PY'
import json
import sys
import urllib.error
import urllib.request

base = sys.argv[1].rstrip("/")


def call(method: str, path: str, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


status, health = call("GET", "/healthz")
print("healthz:", status, health)
if status != 200 or health.get("db") != "ok" or health.get("redis") != "ok":
    sys.exit("健康检查未通过。注意 db=ok 只代表 SELECT 1 能连通，不能代替业务验收。")

candidates = [
    {"candidate_id": f"c{i:02d}", "name": f"验收候选{i}", "cost_cents": 1000 + 100 * i, "category": "验收"}
    for i in range(1, 14)
]
payload = {
    "title": "部署验收",
    "initiator_name": "deploy-check",
    "candidates": candidates,
    "budget_cents": 100000,
    "exact_count": 3,
    "round_limit": 3,
}
status, created = call("POST", "/negotiations", payload)
print("create:", status, created if status != 201 else {"negotiation_id": created.get("negotiation_id")})
if status != 201:
    sys.exit("业务验收失败：创建协商未返回 201，通常是迁移未执行或表结构不匹配。")

negotiation_id = created["negotiation_id"]
status, public = call("GET", f"/negotiations/{negotiation_id}/public")
print("public:", status, public.get("status") if isinstance(public, dict) else public)
if status != 200:
    sys.exit("业务验收失败：公共视图不可读。")

print("业务验收通过：negotiation_id=%s" % negotiation_id)
PY
