"""Production entry point for the mock agent."""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import perf_counter
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from redis.exceptions import RedisError

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import MOCK_RESPONSES, ask as llm_ask

logger = logging.getLogger("day14.agent")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(handler)
logger.propagate = False

# The mock has a fixed set of responses, so the longest response bounds its cost.
_MAX_OUTPUT_TOKENS = max(len(answer.split()) for answers in MOCK_RESPONSES.values() for answer in answers)


def log_event(event: str, **fields: object) -> None:
    logger.info(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "event": event,
        **fields,
    }))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log_event("startup", environment=settings.environment)
    try:
        yield
    finally:
        r.close()
        log_event("shutdown")


app = FastAPI(title="Production AI Agent", lifespan=lifespan)


class AskRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


@app.middleware("http")
async def log_request(request: Request, call_next):
    started = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        log_event(
            "request",
            method=request.method,
            path=request.url.path,
            status=status,
            ms=round((perf_counter() - started) * 1000, 2),
        )


@app.exception_handler(RedisError)
async def redis_unavailable(_request: Request, _exc: RedisError):
    return JSONResponse(status_code=503, content={"detail": "Redis unavailable"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    if redis_healthy():
        return {"status": "ready", "redis": "connected"}
    return JSONResponse(status_code=503, content={"status": "unavailable", "redis": "unreachable"})


@app.post("/ask")
def ask(body: AskRequest, key: Annotated[str, Depends(verify_api_key)]):
    check_rate_limit(key)
    input_tokens = len(body.question.split())
    reservation = check_budget(input_tokens, _MAX_OUTPUT_TOKENS)
    try:
        answer = llm_ask(body.question)
    except Exception:
        record_cost(reservation, 0, 0)
        raise

    cost = record_cost(reservation, input_tokens, len(answer.split()))
    return {"question": body.question, "answer": answer, "cost_usd": cost}
