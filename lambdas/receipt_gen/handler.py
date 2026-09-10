"""Python Lambda: S3 event -> render a receipt -> write it back to S3.

Exists so you can benchmark it against the same logic in a pod (Day 5 /
docs/LAMBDA-VS-POD.md): cold start, p95, and cost per 1M invocations.

Gotchas worth knowing out loud:
  - a Lambda in a VPC gets an ENI and then needs a NAT to reach the internet
  - reserved concurrency is what stops a Lambda stampede exhausting RDS
"""
import json
import os
import urllib.parse

import boto3

s3 = boto3.client("s3")
BUCKET = os.environ.get("RECEIPT_BUCKET", "")


def handler(event, context):
    out = []
    for record in event.get("Records", []):
        src = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        body = json.loads(s3.get_object(Bucket=src, Key=key)["Body"].read())

        receipt = (
            f"SHOPGLOBE RECEIPT\n"
            f"order:  {body.get('order_id')}\n"
            f"user:   {body.get('user_id')}\n"
            f"amount: {body.get('amount')}\n"
            f"market: {body.get('market', 'in')}\n"
            f"request_id: {body.get('request_id', '-')}\n"
        )
        dest = f"receipts/{body.get('order_id', 'unknown')}.txt"
        s3.put_object(Bucket=BUCKET or src, Key=dest,
                      Body=receipt.encode(), ContentType="text/plain",
                      CacheControl="private, max-age=0, no-store")
        out.append(dest)

    # Structured, single-line log -- CloudWatch Logs Insights can query it.
    print(json.dumps({"event": "receipts_written", "count": len(out), "keys": out}))
    return {"statusCode": 200, "written": out}
