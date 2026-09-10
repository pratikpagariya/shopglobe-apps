"""media-svc: S3 presigned URLs behind CloudFront.

The interesting parts are cache correctness and cost:
  - presigned URLs mean bytes never pass through this pod (no egress via NAT)
  - VERSIONED KEYS mean you never pay for a CloudFront invalidation
"""
import hashlib
import time

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException
from pydantic import BaseModel

from libs.common import Settings
from libs.common.app import make_app, run

S = Settings(service_name="media-svc")
s3 = None


async def _startup() -> None:
    global s3
    s3 = boto3.client("s3", **S.boto_kwargs)


async def _check_bucket() -> None:
    s3.head_bucket(Bucket=S.s3_bucket)


app = make_app(S, {"s3": _check_bucket}, _startup)


class UploadRequest(BaseModel):
    filename: str
    content_type: str = "image/jpeg"


@app.post("/media/presign")
async def presign_upload(req: UploadRequest) -> dict:
    """VERSIONED KEY. The content hash is in the object key, so a new version
    gets a new URL. That is why you never need an invalidation: the old URL is
    still valid and still cached, and nothing points at it any more."""
    version = hashlib.sha256(f"{req.filename}{time.time()}".encode()).hexdigest()[:10]
    key = f"media/{version}/{req.filename}"
    try:
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": S.s3_bucket, "Key": key, "ContentType": req.content_type,
                    # Immutable: safe because the key changes when content does.
                    "CacheControl": "public, max-age=31536000, immutable"},
            ExpiresIn=900,
        )
    except ClientError as exc:
        raise HTTPException(502, f"s3 error: {exc}")
    return {"upload_url": url, "key": key, "expires_in": 900,
            "cdn_url": f"https://{S.cdn_domain}/{key}" if S.cdn_domain else None}


@app.get("/media/{key:path}/url")
async def get_url(key: str) -> dict:
    try:
        s3.head_object(Bucket=S.s3_bucket, Key=key)
    except ClientError:
        raise HTTPException(404, "object not found")
    if S.cdn_domain:
        # Serve through the CDN: cached at the edge, and the bucket stays
        # private behind Origin Access Control.
        return {"url": f"https://{S.cdn_domain}/{key}", "via": "cloudfront"}
    url = s3.generate_presigned_url("get_object",
                                    Params={"Bucket": S.s3_bucket, "Key": key},
                                    ExpiresIn=3600)
    return {"url": url, "via": "s3-presigned"}


if __name__ == "__main__":
    run(app, S)
