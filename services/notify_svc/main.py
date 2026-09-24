"""notify-svc: publishes to SNS. Its sibling is lambdas/receipt_gen, which does
the same job as a function so you can compare cold start and cost."""

import boto3
from fastapi import HTTPException
from pydantic import BaseModel

from libs.common import Settings, request_id_var
from libs.common.app import make_app, run

S = Settings(service_name="notify-svc")
sns = None


async def _startup() -> None:
    global sns
    sns = boto3.client("sns", **S.boto_kwargs)


async def _check_sns() -> None:
    sns.get_topic_attributes(TopicArn=S.sns_topic_arn)


app = make_app(S, {"sns": _check_sns}, _startup)


class Notification(BaseModel):
    user_id: str
    channel: str = "email"
    subject: str
    body: str


@app.post("/notify", status_code=202)
async def notify(n: Notification) -> dict:
    if n.channel not in ("email", "sms", "push"):
        raise HTTPException(422, f"unsupported channel: {n.channel}")
    resp = sns.publish(
        TopicArn=S.sns_topic_arn,
        Subject=n.subject[:100],
        Message=n.body,
        MessageAttributes={
            "channel": {"DataType": "String", "StringValue": n.channel},
            "market": {"DataType": "String", "StringValue": S.market},
            "request_id": {"DataType": "String", "StringValue": request_id_var.get()},
        },
    )
    return {"message_id": resp["MessageId"], "channel": n.channel}


if __name__ == "__main__":
    run(app, S)
