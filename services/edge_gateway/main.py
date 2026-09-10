"""edge-gateway: the ONLY internet-facing service. ALB -> here -> everything else."""
import asyncio

import httpx
from fastapi import HTTPException, Request, Response

from libs.common import Settings, request_id_var
from libs.common.app import make_app, run

S = Settings(service_name="edge-gateway")
UPSTREAMS = {"auth": S.auth_url, "catalog": S.catalog_url, "cart": S.cart_url,
             "order": S.order_url, "notify": S.notify_url, "media": S.media_url}
client: httpx.AsyncClient | None = None


async def _startup() -> None:
    global client
    client = httpx.AsyncClient(timeout=S.upstream_timeout_seconds)


async def _shutdown() -> None:
    if client:
        await client.aclose()


async def _check_auth() -> None:
    r = await client.get(f"{S.auth_url}/healthz")
    r.raise_for_status()


app = make_app(S, {"auth": _check_auth}, _startup, _shutdown)


@app.api_route("/api/{svc}/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy(svc: str, path: str, request: Request) -> Response:
    base = UPSTREAMS.get(svc)
    if not base:
        raise HTTPException(404, f"unknown upstream: {svc}")
    body = await request.body()
    # Propagating x-request-id is what makes one request traceable across all
    # 8 services in Loki. Drop this header and correlation dies.
    headers = {
        "x-request-id": request_id_var.get(),
        "content-type": request.headers.get("content-type", "application/json"),
    }
    if auth := request.headers.get("authorization"):
        headers["authorization"] = auth

    last: Exception | None = None
    # NOTE: retrying a POST is only safe because order-svc requires an
    # idempotency key. Blind retries on non-idempotent writes duplicate orders.
    for attempt in range(S.upstream_retries + 1):
        try:
            r = await client.request(request.method, f"{base}/{path}", content=body,
                                     headers=headers, params=request.query_params)
            return Response(r.content, r.status_code,
                            media_type=r.headers.get("content-type"))
        except httpx.HTTPError as exc:
            last = exc
            await asyncio.sleep(0.05 * (2 ** attempt))  # exponential backoff
    raise HTTPException(504, f"upstream {svc} unreachable: {last}")


if __name__ == "__main__":
    run(app, S)
