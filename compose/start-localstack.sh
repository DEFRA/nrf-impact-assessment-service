#!/bin/bash
export AWS_REGION=eu-west-2
export AWS_DEFAULT_REGION=eu-west-2
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test

echo "Creating SQS queue for worker..."
aws --endpoint-url=http://localhost:4566 sqs create-queue --queue-name nrf_impact_assessment_queue

echo "LocalStack initialization complete"
