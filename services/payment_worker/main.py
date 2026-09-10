"""payment-worker: SQS consumer. NO public routes -- only /metrics and probes.

This is the service KEDA scales on queue depth (including to zero). It is also
where the classic SQS bug lives: if processing takes longer than the queue's
visibility timeout, SQS hands the same message to another consumer while you
are still working on it, and you process it forever.
"""
import asyncio
import json
import random
import time

import boto3
from prometheus_client import Counter, Gauge, Histogram

from libs.common import Settings, request_id_var
from libs.common.app import make_app, run

S = Settings(service_name="payment-worker")
PROCESSED = Counter("queue_messages_processed_total", "Processed", ["service", "result"])
DURATION = Histogram("queue_processing_duration_seconds", "Processing time", ["service"],
                     buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
DEPTH = Gauge("queue_depth", "ApproximateNumberOfMessages", ["service"])
AGE = Gauge("queue_oldest_message_age_seconds", "Oldest message age", ["service"])

sqs = None
task: asyncio.Task | None = None
running = True


async def _poll_forever() -> None:
    """Long polling (WaitTimeSeconds=20): one API call parked for 20s instead of
    20 empty calls a second. Cheaper AND lower latency."""
    global running
    while running:
        try:
            resp = sqs.receive_message(
                QueueUrl=S.sqs_queue_url, MaxNumberOfMessages=10,
                WaitTimeSeconds=20, AttributeNames=["ApproximateReceiveCount"],
            )
            for msg in resp.get("Messages", []):
                await _handle(msg)
            attrs = sqs.get_queue_attributes(
                QueueUrl=S.sqs_queue_url,
                AttributeNames=["ApproximateNumberOfMessages",
                                "ApproximateAgeOfOldestMessage"],
            )["Attributes"]
            DEPTH.labels(S.service_name).set(int(attrs.get("ApproximateNumberOfMessages", 0)))
            AGE.labels(S.service_name).set(int(attrs.get("ApproximateAgeOfOldestMessage", 0)))
        except Exception:
            await asyncio.sleep(2)


async def _handle(msg: dict) -> None:
    started = time.perf_counter()
    receives = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", 1))
    try:
        body = json.loads(msg["Body"])
        request_id_var.set(body.get("request_id", "-"))
        # Simulated provider call. Keep this WELL under the queue's visibility
        # timeout (set it to ~6x this in Terraform) or you get redelivery.
        await asyncio.sleep(random.uniform(0.05, 0.4))
        if body.get("amount", 0) > 100000:
            raise ValueError("amount exceeds provider limit")  # -> retries -> DLQ
        sqs.delete_message(QueueUrl=S.sqs_queue_url, ReceiptHandle=msg["ReceiptHandle"])
        PROCESSED.labels(S.service_name, "ok").inc()
    except Exception:
        # Do NOT delete. SQS redelivers; after maxReceiveCount it goes to the
        # DLQ so one poison message cannot block the queue.
        PROCESSED.labels(S.service_name, "failed").inc()
    finally:
        DURATION.labels(S.service_name).observe(time.perf_counter() - started)
        del receives


async def _startup() -> None:
    global sqs, task
    sqs = boto3.client("sqs", **S.boto_kwargs)
    task = asyncio.create_task(_poll_forever())


async def _shutdown() -> None:
    global running
    running = False
    if task:
        task.cancel()


async def _check_sqs() -> None:
    sqs.get_queue_attributes(QueueUrl=S.sqs_queue_url, AttributeNames=["QueueArn"])


app = make_app(S, {"sqs": _check_sqs}, _startup, _shutdown)

if __name__ == "__main__":
    run(app, S)
