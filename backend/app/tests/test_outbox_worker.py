"""outbox 投影 worker：版本门控、幂等重放、崩溃恢复、死信。

Redis 语义由 FakeRedis 按同一门控契约实现；真实 Lua 行为在云上部署时
用 health/录屏验收覆盖（P1）。
"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.models.outbox import Outbox
from app.tests.test_api_flow import _answer, _me, _start
from app.workers import outbox_projection as worker


class FakeRedis:
    """实现 _VERSION_GATE_LUA 的门控契约：版本未升高则拒绝。"""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, object]] = {}

    def eval(self, script: str, numkeys: int, key: str, version: str, view: str, ttl: str):
        entry = self.store.get(key)
        if entry is not None and int(entry["version"]) >= int(version):
            return 0
        self.store[key] = {"version": int(version), "view": view, "ttl": int(ttl)}
        return 1


@pytest.fixture()
def fake_redis():
    return FakeRedis()


def test_projects_public_and_member_snapshots_without_cross_member_leak(api, fake_redis):
    client, factory = api
    negotiation_id, headers = _start(client, round_limit=2)

    with factory() as db:
        stats = worker.process_pending(db, fake_redis)
    assert stats["committed"] > 0

    public = fake_redis.store[worker.public_key(negotiation_id)]
    view = json.loads(public["view"])  # type: ignore[index]
    assert view["negotiation_id"] == negotiation_id
    public_text = public["view"]  # type: ignore[index]
    for forbidden in ("token", "top_id", "acceptable_ids", "question_batch", "小林"):
        assert forbidden not in public_text

    from sqlalchemy import select

    from app.models.business import Member

    with factory() as db:
        members = db.scalars(
            select(Member).where(Member.negotiation_id == negotiation_id)
        ).all()
        assert len(members) == 2
        names = {member.id: member.display_name for member in members}

    for member in members:
        key = worker.member_key(negotiation_id, member.id)
        assert key in fake_redis.store
        my_text = fake_redis.store[key]["view"]  # type: ignore[index]
        my_view = json.loads(my_text)
        assert my_view["member"]["member_id"] == member.id
        # 本人快照不得出现其他成员的标识数据
        for other_id, other_name in names.items():
            if other_id == member.id:
                continue
            if other_name:
                assert other_name not in my_text


def test_new_event_bumps_version_and_stale_write_rejected(api, fake_redis):
    client, factory = api
    negotiation_id, headers = _start(client, round_limit=2)

    with factory() as db:
        worker.process_pending(db, fake_redis)
    key = worker.public_key(negotiation_id)
    first_version = fake_redis.store[key]["version"]  # type: ignore[index]

    me = _me(client, negotiation_id, headers["a"])
    response = _answer(client, negotiation_id, headers["a"], me["question_batch"])
    assert response.status_code == 200

    with factory() as db:
        worker.process_pending(db, fake_redis)
    assert fake_redis.store[key]["version"] > first_version  # type: ignore[index]

    latest_view = fake_redis.store[key]["view"]  # type: ignore[index]
    stale = worker.write_snapshot(
        fake_redis, key, int(first_version), latest_view, settings.redis_ttl_seconds
    )
    assert stale is False
    assert fake_redis.store[key]["version"] > first_version  # type: ignore[index]


def test_crash_before_mark_retries_idempotently(api, fake_redis):
    client, factory = api
    negotiation_id, headers = _start(client, round_limit=2)

    from sqlalchemy import select

    with factory() as db:
        pending = db.scalars(
            select(Outbox).where(Outbox.status == "pending")
        ).all()
        assert pending
        # 模拟 worker 在 Redis 写成功后、标记 outbox 前崩溃
        worker.project_negotiation(db, fake_redis, negotiation_id)
        snapshots = json.dumps(fake_redis.store, sort_keys=True)

    with factory() as db:
        stats = worker.process_pending(db, fake_redis)

    assert stats["projected"] == 0  # 同版本重放被门控拒绝
    assert stats["committed"] == len(pending)
    assert json.dumps(fake_redis.store, sort_keys=True) == snapshots

    with factory() as db:
        rows = db.scalars(select(Outbox).where(Outbox.negotiation_id == negotiation_id)).all()
    assert all(row.status == "done" for row in rows)
    assert all(row.attempts == 1 for row in rows)


def test_poison_row_dead_letters_after_max_attempts(api, fake_redis, monkeypatch):
    client, factory = api
    negotiation_id, _ = _start(client, round_limit=2)
    monkeypatch.setattr(settings, "outbox_max_attempts", 2)

    def boom(db, client, negotiation_id):
        raise RuntimeError("project exploded")

    monkeypatch.setattr(worker, "project_negotiation", boom)

    from sqlalchemy import select

    with factory() as db:
        worker.process_pending(db, fake_redis)
        rows = db.scalars(select(Outbox).where(Outbox.negotiation_id == negotiation_id)).all()
        assert rows
        assert all(row.status == "pending" for row in rows)
        assert all(row.attempts == 1 for row in rows)

        worker.process_pending(db, fake_redis)
        rows = db.scalars(select(Outbox).where(Outbox.negotiation_id == negotiation_id)).all()
        assert all(row.status == "failed" for row in rows)
        assert all("project exploded" in (row.last_error or "") for row in rows)


def test_rebuild_unknown_negotiation_is_noop(fake_redis):
    from app.tests.test_api_flow import _candidates  # noqa: F401  确认测试模块可导入

    class _NullSession:
        def get(self, model, key):
            return None

    assert worker.project_negotiation(_NullSession(), fake_redis, 424242) == 0
