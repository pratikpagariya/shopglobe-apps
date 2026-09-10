#!/bin/sh
# Runs inside localstack once it is ready. Creates the AWS resources the
# services expect, so `docker compose up` needs no manual setup.
set -e
awslocal sqs create-queue --queue-name payments-dlq
DLQ=$(awslocal sqs get-queue-attributes --queue-url http://localhost:4566/000000000000/payments-dlq \
      --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)
awslocal sqs create-queue --queue-name payments --attributes \
  "{\"VisibilityTimeout\":\"30\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}"
awslocal sns create-topic --name notifications
awslocal s3 mb s3://shopglobe-media
echo "localstack ready: sqs payments(+dlq, visibility=30s, maxReceive=3), sns notifications, s3 shopglobe-media"
