"""payment-worker regression tests, both from bugs found on EKS.

1. receive_message(WaitTimeSeconds=20) is a BLOCKING boto3 call. Called
   directly inside the event loop -- and with no other await on the
   empty-queue path -- it starved the loop: /healthz and /readyz never got a
   turn, the pod flapped Ready/NotReady and liveness kept restarting it.
   docker-compose never showed it: compose has no probes.
2. It asked SQS for ApproximateAgeOfOldestMessage, which is a CloudWatch
   metric, not a queue attribute. Every call failed with InvalidAttributeName,
   swallowed by a bare except, so the queue gauges were never set.
"""

import threading
import time

from fastapi.testclient import TestClient

# Every attribute GetQueueAttributes accepts. Anything else is rejected by AWS.
SQS_QUEUE_ATTRIBUTES = {
    "All", "Policy", "VisibilityTimeout", "MaximumMessageSize", "MessageRetentionPeriod",
    "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
    "ApproximateNumberOfMessagesDelayed", "CreatedTimestamp", "LastModifiedTimestamp",
    "QueueArn", "DelaySeconds", "ReceiveMessageWaitTimeSeconds", "RedrivePolicy",
    "RedriveAllowPolicy", "FifoQueue", "ContentBasedDeduplication", "KmsMasterKeyId",
    "KmsDataKeyReusePeriodSeconds", "DeduplicationScope", "FifoThroughputLimit",
    "SqsManagedSseEnabled",
}  # fmt: skip


def test_probes_answer_while_long_polling(monkeypatch):
    from services.payment_worker import main

    asked: list[str] = []

    def slow_receive(**_kwargs):
        time.sleep(3)  # stands in for a 20s long poll on an empty queue
        return {"Messages": []}

    def get_queue_attributes(**kwargs):
        asked.extend(kwargs["AttributeNames"])
        return {"Attributes": {}}

    monkeypatch.setattr(main.sqs, "receive_message", slow_receive)
    monkeypatch.setattr(main.sqs, "get_queue_attributes", get_queue_attributes)
    monkeypatch.setattr(main.S, "drain_seconds", 0)

    result: dict = {}

    def probe() -> None:
        with TestClient(main.app) as client:
            time.sleep(0.3)  # let the poll task enter receive_message
            started = time.perf_counter()
            result["status"] = client.get("/healthz").status_code
            result["elapsed"] = time.perf_counter() - started
            # Stay up for one full poll cycle, so the attribute call happens.
            deadline = time.monotonic() + 6
            while not asked and time.monotonic() < deadline:
                time.sleep(0.1)

    # In a watchdog thread: if the loop is starved, the request never returns.
    # Fail in seconds instead of hanging the whole test run.
    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    worker.join(timeout=30)  # a healthy run takes ~7s; headroom for a slow CI agent

    assert not worker.is_alive(), "event loop starved: /healthz never answered"
    assert result["status"] == 200
    assert result["elapsed"] < 1.0, "probe slower than the kubelet's 1s timeout"
    assert asked, "the poll loop never reached get_queue_attributes"
    invalid = set(asked) - SQS_QUEUE_ATTRIBUTES
    assert not invalid, f"not SQS queue attributes (AWS rejects them): {invalid}"
