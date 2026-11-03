from __future__ import annotations

import redis

from app.core.config import settings


def get_client() -> redis.Redis:
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=settings.health_timeout_seconds,
        socket_timeout=settings.health_timeout_seconds,
    )


def ping_redis() -> bool:
    client = get_client()
    try:
        return bool(client.ping())
    except Exception:
        return False
    finally:
        client.close()
