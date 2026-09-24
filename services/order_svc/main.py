"""order-svc: writes the order, then hands payment to SQS and returns 202.

Async decoupling: the user does not wait for a payment provider. The cost is
that you must now handle at-least-once delivery, which is what the idempotency
key is for.
"""

import json
import uuid

import boto3
import httpx
from fastapi import HTTPException
from pydantic import BaseModel

from libs.common import Settings, request_id_var
from libs.common.app import make_app, run

S = Settings(service_name="order-svc")
sqs = None
client: httpx.AsyncClient | None = None
ORDERS: dict[str, dict] = {}
SEEN: dict[str, str] = {}  # idempotency_key -> order_id


async def _startup() -> None:
    global sqs, client
    sqs = boto3.client("sqs", **S.boto_kwargs)
    client = httpx.AsyncClient(timeout=S.upstream_timeout_seconds)


async def _shutdown() -> None:
    if client:
        await client.aclose()


async def _check_sqs() -> None:
    sqs.get_queue_attributes(QueueUrl=S.sqs_queue_url, AttributeNames=["QueueArn"])


async def _check_cart() -> None:
    r = await client.get(f"{S.cart_url}/healthz")
    r.raise_for_status()


app = make_app(S, {"sqs": _check_sqs, "cart": _check_cart}, _startup, _shutdown)


class OrderRequest(BaseModel):
    user_id: str
    amount: float
    idempotency_key: str


@app.post("/orders", status_code=202)
async def create_order(req: OrderRequest) -> dict:
    # IDEMPOTENCY. SQS is at-least-once and clients retry, so the same order
    # will arrive twice. Storing the key (in Postgres, with a UNIQUE index, in
    # the real thing) is the only defence.
    if existing := SEEN.get(req.idempotency_key):
        return {"order_id": existing, "status": "duplicate_ignored"}

    if req.amount <= 0:
        raise HTTPException(422, "amount must be positive")

    order_id = uuid.uuid4().hex[:12]
    ORDERS[order_id] = {"user_id": req.user_id, "amount": req.amount, "status": "pending"}
    SEEN[req.idempotency_key] = order_id

    sqs.send_message(
        QueueUrl=S.sqs_queue_url,
        MessageBody=json.dumps(
            {
                "order_id": order_id,
                "user_id": req.user_id,
                "amount": req.amount,
                "idempotency_key": req.idempotency_key,
                "request_id": request_id_var.get(),
            }
        ),
    )
    return {"order_id": order_id, "status": "pending"}


@app.get("/orders/{order_id}")
async def get_order(order_id: str) -> dict:
    if order_id not in ORDERS:
        raise HTTPException(404, "order not found")
    return {"order_id": order_id, **ORDERS[order_id]}


if __name__ == "__main__":
    run(app, S)
