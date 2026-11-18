from __future__ import annotations

from functools import lru_cache
import redis

from app.core.config import settings


@lru_cache(maxsize=1)
def get_client() -> redis.Redis:
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )


def ping_redis() -> bool:
    client = redis.Redis.from_url(settings.redis_url,
        socket_connect_timeout=settings.health_timeout_seconds,
        socket_timeout=settings.health_timeout_seconds)
    try:
        return bool(client.ping())
    except Exception:
        return False
    finally:
        client.close()
