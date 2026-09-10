"""cart-svc: ElastiCache Redis cache-aside over catalog-svc.

The point of this service: Redis is a CACHE, so losing it must DEGRADE us
(slower, straight to catalog) not BREAK us. That is why redis is deliberately
NOT in the readiness checks.
"""
import json
import random

import httpx
import redis.asyncio as redis
from fastapi import HTTPException
from prometheus_client import Counter
from pydantic import BaseModel

from libs.common import Settings, request_id_var
from libs.common.app import make_app, run

S = Settings(service_name="cart-svc")
HITS = Counter("cache_hits_total", "Cache hits", ["service"])
MISSES = Counter("cache_misses_total", "Cache misses", ["service"])
ERRORS = Counter("cache_errors_total", "Cache unavailable", ["service"])

rds: redis.Redis | None = None
client: httpx.AsyncClient | None = None
CARTS: dict[str, list[int]] = {}  # demo-only; real impl stores this in Redis


async def _startup() -> None:
    global rds, client
    rds = redis.from_url(S.redis_url, socket_timeout=0.25, decode_responses=True)
    client = httpx.AsyncClient(timeout=S.upstream_timeout_seconds)


async def _shutdown() -> None:
    if rds:
        await rds.aclose()
    if client:
        await client.aclose()


async def _check_catalog() -> None:
    r = await client.get(f"{S.catalog_url}/healthz")
    r.raise_for_status()


# Only catalog is a hard dependency. Redis is not -- see module docstring.
app = make_app(S, {"catalog": _check_catalog}, _startup, _shutdown)


async def _product(pid: int) -> dict:
    key = f"product:{pid}"
    try:
        if cached := await rds.get(key):
            HITS.labels(S.service_name).inc()
            return json.loads(cached)
        MISSES.labels(S.service_name).inc()
    except Exception:
        # Redis down: count it, log nothing per-request, fall through to origin.
        ERRORS.labels(S.service_name).inc()

    r = await client.get(f"{S.catalog_url}/products/{pid}",
                         headers={"x-request-id": request_id_var.get()})
    if r.status_code == 404:
        raise HTTPException(404, "product not found")
    r.raise_for_status()
    data = r.json()
    try:
        # TTL JITTER. Without it, 10k keys written in the same second expire in
        # the same second, every request misses at once, and the stampede lands
        # on Postgres. +/-20% spreads the expiry.
        ttl = int(S.cache_ttl_seconds * random.uniform(0.8, 1.2))
        await rds.setex(key, ttl, json.dumps(data))
    except Exception:
        ERRORS.labels(S.service_name).inc()
    return data


class AddItem(BaseModel):
    product_id: int


@app.post("/cart/{user_id}/items")
async def add_item(user_id: str, item: AddItem) -> dict:
    product = await _product(item.product_id)
    CARTS.setdefault(user_id, []).append(item.product_id)
    return {"user_id": user_id, "added": product, "size": len(CARTS[user_id])}


@app.get("/cart/{user_id}")
async def get_cart(user_id: str) -> dict:
    ids = CARTS.get(user_id, [])
    items = [await _product(i) for i in ids]
    return {"user_id": user_id, "items": items,
            "total": round(sum(i["price"] for i in items), 2)}


@app.delete("/cart/{user_id}")
async def clear(user_id: str) -> dict:
    CARTS.pop(user_id, None)
    return {"cleared": True}


if __name__ == "__main__":
    run(app, S)
