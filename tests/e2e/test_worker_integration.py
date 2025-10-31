"""End-to-end integration test for worker with LocalStack and Docker Compose."""

import json
import subprocess
import time

import boto3
import httpx
import pytest


@pytest.fixture(scope="module")
def docker_services():
    """Start docker compose services for testing."""
    # Start services with worker profile
    subprocess.run(
        ["docker", "compose", "--profile", "worker", "up", "-d", "--build"],
        check=True,
        capture_output=True,
    )

    # Wait for services to be healthy (build + startup can take time)
    time.sleep(30)

    yield

    # Cleanup
    subprocess.run(
        ["docker", "compose", "--profile", "worker", "down", "-v"],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def sqs_client():
    """Create SQS client for LocalStack."""
    return boto3.client(
        "sqs",
        region_name="eu-west-2",
        endpoint_url="http://localhost:4566",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def test_worker_integration(docker_services, sqs_client):  # noqa: ARG001
    """Test complete worker flow: health check, SQS message processing.

    Steps:
    1. Check health endpoint is responding
    2. Send test message to SQS queue
    3. Check worker logs for message processing
    4. Verify message was deleted from queue
    """
    # Step 1: Check health endpoint
    health_url = "http://localhost:8085/health"
    max_retries = 10
    for i in range(max_retries):
        try:
            response = httpx.get(health_url, timeout=2)
            if response.status_code == 200:
                data = response.json()
                assert data["status"] == "ok"
                break
        except httpx.HTTPError:
            if i == max_retries - 1:
                raise
            time.sleep(2)

    # Step 2: Send test message to SQS
    queue_url = "http://localhost:4566/000000000000/nrf-assessment-queue"
    test_message = {"test": "hello world", "timestamp": time.time()}

    sqs_client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(test_message),
    )

    # Step 3: Wait for worker to process message (check logs)
    time.sleep(5)

    result = subprocess.run(
        ["docker", "compose", "logs", "worker"],
        capture_output=True,
        text=True,
    )
    logs = result.stdout

    # Verify logs contain expected processing messages
    assert "Received message:" in logs, "Worker should log received message"
    assert "hello world" in logs, "Worker should log message body"
    assert "Deleted message:" in logs, "Worker should log message deletion"

    # Step 4: Verify queue is empty (message was deleted)
    response = sqs_client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=1,
    )

    messages = response.get("Messages", [])
    assert len(messages) == 0, "Queue should be empty after processing"
