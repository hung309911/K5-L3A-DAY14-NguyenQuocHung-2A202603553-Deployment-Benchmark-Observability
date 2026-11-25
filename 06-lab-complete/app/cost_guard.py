"""Reserve the mock LLM's maximum cost atomically, then settle the actual usage."""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# USD 0.00015 per 1K input tokens and USD 0.0006 per 1K output tokens.
_NANO_PER_USD = 1_000_000_000
_INPUT_PRICE = 150
_OUTPUT_PRICE = 600

_RESERVE_COST = r.register_script("""
local key = KEYS[1]
local charge = tonumber(ARGV[1])
local budget = tonumber(ARGV[2])
local spent = tonumber(redis.call('GET', key) or '0')
if spent + charge > budget then
    return 0
end
redis.call('INCRBY', key, charge)
redis.call('EXPIREAT', key, ARGV[3])
return 1
""")

_RELEASE_COST = r.register_script("""
if redis.call('EXISTS', KEYS[1]) == 1 then
    redis.call('DECRBY', KEYS[1], ARGV[1])
end
""")


def _price(input_tokens: int, output_tokens: int) -> int:
    return input_tokens * _INPUT_PRICE + output_tokens * _OUTPUT_PRICE


def check_budget(input_tokens: int, max_output_tokens: int) -> tuple[str, int]:
    """Reserve a charge against the current UTC day's budget."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    key = f"day14:cost:{now.date().isoformat()}"
    reserved = _price(input_tokens, max_output_tokens)
    budget = int(settings.daily_budget_usd * _NANO_PER_USD)
    if not _RESERVE_COST(keys=[key], args=[reserved, budget, int(tomorrow.timestamp())]):
        raise HTTPException(status_code=402, detail="Daily budget exceeded")
    return key, reserved


def record_cost(reservation: tuple[str, int], input_tokens: int, output_tokens: int) -> float:
    """Refund unused budget and return the actual cost in USD."""
    key, reserved = reservation
    cost = _price(input_tokens, output_tokens)
    if reserved != cost:
        _RELEASE_COST(keys=[key], args=[reserved - cost])
    return cost / _NANO_PER_USD
