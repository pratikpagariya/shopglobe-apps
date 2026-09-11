#!/usr/bin/env bash
# The legacy deploy. Time the whole thing -- it is your lead-time baseline.
set -euo pipefail
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="shopglobe-artifacts-$ACCT"
REGION=ap-south-1

aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null || \
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"

tar czf /tmp/app.tar.gz libs services requirements.txt seed.sql
aws s3 cp /tmp/app.tar.gz "s3://$BUCKET/app.tar.gz" --region "$REGION"
echo "artifact_bucket = \"$BUCKET\""
