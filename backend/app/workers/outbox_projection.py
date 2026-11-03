"""outbox 投影 worker：RDS 先落事实，本 worker 按 state_version 幂等写 Redis。

设计要点（技术设计 §10.4）：
- Redis Lua 只接受更高版本，重放同一 state_version 是无操作；
- worker 在 Redis 写成功但 outbox 未标记时崩溃，重试仍幂等；
- 公共快照与成员快照分键，成员快照永不作为公共响应；
- 缓存丢失后的重建走 project_negotiation 同一函数，是常态路径不是异常路径。
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.cache.redis import get_client
from app.core.config import settings
from app.engine.projection import build_my_view, build_public_view
from app.models.business import Member, Negotiation
from app.models.outbox import Outbox

logger = logging.getLogger(__name__)

# KEYS[1]=快照键；ARGV[1]=版本，ARGV[2]=视图 JSON，ARGV[3]=TTL。
# 版本未升高时拒绝写入，使重放与并发 worker 都幂等。
_VERSION_GATE_LUA = """
local current = redis.call('HGET', KEYS[1], 'version')
if current and tonumber(current) >= tonumber(ARGV[1]) then
  return 0
end
redis.call('HSET', KEYS[1], 'version', ARGV[1], 'view', ARGV[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 1
"""


def public_key(negotiation_id: int) -> str:
    return f"pn:{negotiation_id}:public"


def member_key(negotiation_id: int, member_id: int) -> str:
    return f"pn:{negotiation_id}:member:{member_id}"


def write_snapshot(
    client: Any, key: str, state_version: int, view_json: str, ttl: int
) -> bool:
    """带版本门控的快照写入；False 表示版本未升高（重放或过期事件）。"""
    result = client.eval(
        _VERSION_GATE_LUA, 1, key, str(state_version), view_json, str(ttl)
    )
    return int(result) == 1


def project_negotiation(
    db: Session, client: Any, negotiation_id: int, *, ttl: int | None = None
) -> int:
    """把协商当前投影写入 Redis；缓存丢失后的重建调用同一函数。返回实际写入数。"""
    negotiation = db.get(Negotiation, negotiation_id, populate_existing=True)
    if negotiation is None:
        return 0
    ttl = settings.redis_ttl_seconds if ttl is None else ttl
    version = negotiation.state_version
    written = 0

    public_json = build_public_view(db, negotiation).model_dump_json()
    if write_snapshot(client, public_key(negotiation_id), version, public_json, ttl):
        written += 1

    members = db.scalars(
        select(Member).where(
            Member.negotiation_id == negotiation_id
        ).execution_options(populate_existing=True)
    ).all()
    for member in members:
        member_json = (build_my_view(db, negotiation, member).model_dump_json()
                       if member.active and not member.token_revoked
                       else json.dumps({"membership_status": member.membership_status,
                                        "access": "revoked" if member.token_revoked else "retained_constraints_only"}))
        if write_snapshot(
            client, member_key(negotiation_id, member.id), version, member_json, ttl
        ):
            written += 1
    return written


def _mark_done(db: Session, row: Outbox) -> bool:
    result = db.execute(
        update(Outbox)
        .where(Outbox.id == row.id, Outbox.status == "pending")
        .values(
            status="done",
            attempts=Outbox.attempts + 1,
            published_at=func.now(),
        )
    )
    return bool(result.rowcount)


def _mark_failure(db: Session, row: Outbox, error: Exception) -> None:
    attempts = row.attempts + 1
    dead = attempts >= settings.outbox_max_attempts
    db.execute(
        update(Outbox)
        .where(Outbox.id == row.id, Outbox.status == "pending")
        .values(
            status="failed" if dead else "pending",
            attempts=attempts,
            last_error=f"{type(error).__name__}: {error}"[:500],
        )
    )


def _sweep_dead_letters(db: Session) -> int:
    """把反复失败的 pending 行置为 failed，避免阻塞队头；可人工重置重试。"""
    result = db.execute(
        update(Outbox)
        .where(
            Outbox.status == "pending",
            Outbox.attempts >= settings.outbox_max_attempts,
        )
        .values(status="failed", last_error="max_attempts_exceeded")
    )
    return int(result.rowcount or 0)


def process_pending(db: Session, client: Any, *, limit: int | None = None) -> dict[str, int]:
    """消费一批 pending outbox：按协商去重投影一次，CAS 标记完成。"""
    rows = db.scalars(
        select(Outbox)
        .where(Outbox.status == "pending", Outbox.attempts < settings.outbox_max_attempts)
        .order_by(Outbox.id)
        .limit(limit or settings.outbox_batch_size)
    ).all()
    grouped: dict[int, list[Outbox]] = defaultdict(list)
    for row in rows:
        grouped[row.negotiation_id].append(row)

    stats = {"projected": 0, "committed": 0, "failed": 0}
    for negotiation_id, group in grouped.items():
        try:
            stats["projected"] += project_negotiation(db, client, negotiation_id)
        except Exception as exc:
            db.rollback()
            for row in group:
                _mark_failure(db, row, exc)
            db.commit()
            stats["failed"] += len(group)
            continue
        for row in group:
            if _mark_done(db, row):
                stats["committed"] += 1
        db.commit()

    dead = _sweep_dead_letters(db)
    if dead:
        db.commit()
        stats["dead_lettered"] = dead
    return stats


def main() -> None:
    logging.basicConfig(level=settings.log_level)
    parser = argparse.ArgumentParser(description="outbox → Redis 投影 worker")
    parser.add_argument("--once", action="store_true", help="处理一批后退出")
    parser.add_argument(
        "--loop", action="store_true", help=f"按 {settings.worker_loop_interval_seconds}s 间隔循环"
    )
    parser.add_argument(
        "--rebuild",
        type=int,
        metavar="NEGOTIATION_ID",
        help="重建指定协商的 Redis 快照后退出（缓存丢失恢复路径）",
    )
    args = parser.parse_args()

    client = get_client()
    from app.db.session import SessionLocal

    if args.rebuild is not None:
        with SessionLocal() as db:
            written = project_negotiation(db, client, args.rebuild)
        print(json.dumps({"rebuild": args.rebuild, "written": written}))
        return

    if args.loop:
        while True:
            with SessionLocal() as db:
                stats = process_pending(db, client)
            if any(stats.values()):
                logger.info("outbox projection %s", stats)
            time.sleep(settings.worker_loop_interval_seconds)
        return

    with SessionLocal() as db:
        stats = process_pending(db, client)
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
