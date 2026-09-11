#!/usr/bin/env bash
# One-off: apply seed.sql to the legacy RDS instance.
#
# RDS lives in a private subnet with no internet route, so this runs the schema
# FROM an app instance via SSM send-command -- no bastion, no SSH key, no
# public database endpoint.
#
# Deliberately a separate, manually-invoked script rather than part of
# user_data: if this ran on boot, both ASG instances would race to create the
# same tables, and every scale-out event would re-run it. Day 4 replaces this
# with Alembic in an ArgoCD PreSync hook -- one job, before the pods start.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
ASG="shopglobe-legacy-app"

command -v jq >/dev/null || { echo "jq required: sudo apt install -y jq" >&2; exit 1; }

ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="shopglobe-artifacts-$ACCT"

IID=$(aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names "$ASG" --region "$REGION" \
        --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`]|[0].InstanceId' \
        --output text)

if [ -z "$IID" ] || [ "$IID" = "None" ]; then
  echo "No InService instance in $ASG. Is the ASG healthy?" >&2
  exit 1
fi
echo ">> seeding via $IID"

# NOTE: AWS-RunShellScript executes with /bin/sh (dash), NOT bash.
# No `pipefail`, no `{ ...; }` brace groups, no `[[ ]]`.
#
# Credentials are read from the .env that user_data already wrote from Secrets
# Manager, so the password never passes through this script, the SSM API, or
# your shell history.
REMOTE=$(cat <<REMOTE_EOF
set -eu
if ! command -v psql >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-client
fi
aws s3 cp s3://$BUCKET/app.tar.gz /tmp/a.tgz --region $REGION --quiet
tar xzf /tmp/a.tgz -C /tmp seed.sql
PSQLURL=\$(grep '^DATABASE_URL=' /opt/shopglobe/.env | cut -d= -f2- | sed 's|postgresql+asyncpg://|postgresql://|')
psql "\$PSQLURL" -v ON_ERROR_STOP=1 -f /tmp/seed.sql
psql "\$PSQLURL" -t -c 'SELECT COUNT(*) FROM products;'
REMOTE_EOF
)

# Build the parameters as real JSON. The CLI's shorthand `commands="[...]"`
# form splits on commas and corrupts any embedded script -- use a JSON object.
PARAMS=$(jq -n --arg c "$REMOTE" '{commands:[$c]}')

CID=$(aws ssm send-command --instance-ids "$IID" --region "$REGION" \
      --document-name AWS-RunShellScript \
      --parameters "$PARAMS" \
      --query Command.CommandId --output text)

echo ">> command $CID dispatched, waiting..."
STATUS=Pending
for _ in $(seq 1 30); do
  STATUS=$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$IID" \
           --region "$REGION" --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
  sleep 4
done

echo ">> status: $STATUS"
aws ssm get-command-invocation --command-id "$CID" --instance-id "$IID" --region "$REGION" \
  --query '[StandardOutputContent,StandardErrorContent]' --output text

[ "$STATUS" = "Success" ] || exit 1
echo ">> seeded."
