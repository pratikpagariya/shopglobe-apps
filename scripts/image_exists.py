"""Does REPO:IMAGE_TAG already exist in ECR? Exit 0 = yes, 1 = no, 2 = the
check itself failed (credentials, IAM, network, missing env var).

Used by the Jenkinsfile. ECR tags are IMMUTABLE, so rebuilding a commit that
was already pushed would fail at push. Same SHA = same image, so the pipeline
skips the build instead. REGION, REPO and IMAGE_TAG come from the Jenkinsfile's
environment block; credentials come from Pod Identity, like everything else.
"""

import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def main() -> int:
    try:
        region, repo, tag = (os.environ[k] for k in ("REGION", "REPO", "IMAGE_TAG"))
        resp = boto3.client("ecr", region_name=region).batch_get_image(
            repositoryName=repo, imageIds=[{"imageTag": tag}]
        )
    except (KeyError, BotoCoreError, ClientError) as exc:
        print(f"image_exists: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    # A missing tag is NOT an exception: it comes back under "failures" with
    # failureCode ImageNotFound, and "images" is simply empty.
    return 0 if resp["images"] else 1


if __name__ == "__main__":
    sys.exit(main())
