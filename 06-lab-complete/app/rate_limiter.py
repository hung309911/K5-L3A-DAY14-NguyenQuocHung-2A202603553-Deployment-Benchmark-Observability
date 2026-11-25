"""Redis-backed sliding window shared by all workers."""

from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

_WINDOW = 60
_ALLOW_REQUEST = r.register_script("""
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
if limit <= 0 then
    return window
end
if redis.call('ZCARD', key) >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    return math.max(1, math.ceil(tonumber(oldest[2]) + window - now))
end
redis.call('ZADD', key, now, ARGV[3])
redis.call('EXPIRE', key, window)
return 0
""")


def check_rate_limit(key: str) -> None:
    key_digest = sha256(key.encode()).hexdigest()
    retry_after = _ALLOW_REQUEST(
        keys=[f"day14:rate:{key_digest}"],
        args=[_WINDOW, settings.rate_limit_per_minute, uuid4().hex],
    )
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
