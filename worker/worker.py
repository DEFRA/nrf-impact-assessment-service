"""NRF Impact Assessment Worker - SQS polling with health endpoint."""

import logging
import signal
import socket
import sys
import threading
import time
from typing import Any

import boto3
import botocore.exceptions
from mypy_boto3_sqs import SQSClient
from pydantic import BaseModel, Field

from worker.config import WorkerConfig
from worker.health import run_health_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# SQS message models
class SQSMessage(BaseModel):
    """Validated SQS message structure."""

    message_id: str = Field(
        ..., alias="MessageId", description="Unique message identifier"
    )
    body: str = Field(..., alias="Body", description="Message body content")
    receipt_handle: str = Field(
        ..., alias="ReceiptHandle", description="Handle for deleting message"
    )

    # Optional fields that SQS may include
    attributes: dict[str, Any] | None = Field(default=None, alias="Attributes")
    message_attributes: dict[str, Any] | None = Field(
        default=None, alias="MessageAttributes"
    )
    md5_of_body: str | None = Field(default=None, alias="MD5OfBody")

    class Config:
        # Allow extra fields in case AWS adds new ones
        extra = "allow"
        populate_by_name = True


class SQSMessageResponse(BaseModel):
    """Validated SQS receive_message response structure."""

    messages: list[SQSMessage] | None = Field(default=None, alias="Messages")

    class Config:
        extra = "allow"
        populate_by_name = True


def is_port_available(port: int) -> bool:
    """Check if a port is available."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class Worker:
    """SQS polling worker with graceful shutdown."""

    def __init__(self, sqs_client: SQSClient, sqs_queue_url: str):
        """Initialize worker.

        Args:
            sqs_client: Boto3 SQS client
            sqs_queue_url: SQS queue URL to poll
        """
        self.sqs_client = sqs_client
        self.sqs_queue_url = sqs_queue_url
        self.running = True

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):  # noqa: ARG002
        """Handle shutdown signals.

        Args:
            signum: Signal number
            frame: Current stack frame
        """
        logger.info("Received signal %s, shutting down gracefully...", signum)
        self.running = False

    def _delete_message(self, message_id: str, receipt_handle: str) -> None:
        """Delete a message from the queue with error handling.

        Args:
            message_id: The message ID for logging
            receipt_handle: The receipt handle for deletion
        """
        try:
            self.sqs_client.delete_message(
                QueueUrl=self.sqs_queue_url,
                ReceiptHandle=receipt_handle,
            )
            logger.info("Deleted message: %s", message_id)
        except botocore.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ReceiptHandleIsInvalid":
                logger.warning(
                    "Receipt handle expired, message may have already been deleted"
                )
            else:
                logger.error("Failed to delete message %s: %s", message_id, e)
                # Message will become visible again after visibility timeout
                raise

    def _process_messages(self, messages: list[SQSMessage]) -> None:
        """Process a list of messages.

        Args:
            messages: List of validated SQS messages
        """
        for message in messages:
            message_id = message.message_id
            body = message.body
            receipt_handle = message.receipt_handle

            logger.info("Received message: %s", message_id)
            logger.info("Message body: %s", body)

            self._delete_message(message_id, receipt_handle)

    def run(self) -> None:
        """Main polling loop - receives, logs, and deletes SQS messages."""
        logger.info("Worker started, polling for messages...")
        logger.info("Queue URL: %s", self.sqs_queue_url)

        while self.running:
            try:
                # Long polling with 20 second wait time
                response = self.sqs_client.receive_message(
                    QueueUrl=self.sqs_queue_url,
                    MaxNumberOfMessages=1,
                    WaitTimeSeconds=20,
                )

                validated_response = SQSMessageResponse(**response)
                messages = validated_response.messages or []

                if messages:
                    self._process_messages(messages)
                else:
                    logger.debug("No messages received (20s poll timeout)")

            except botocore.exceptions.ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "QueueDoesNotExist":
                    logger.error("Queue does not exist, cannot continue")
                    raise  # Fatal error
                if error_code in (
                    "AccessDenied",
                    "InvalidAccessKeyId",
                    "SignatureDoesNotMatch",
                ):
                    logger.error("Authentication/authorization error: %s", error_code)
                    raise  # Fatal error
                logger.warning("AWS client error: %s, retrying...", error_code)
                time.sleep(5)
            except botocore.exceptions.BotoCoreError as e:
                logger.warning("AWS SDK error (network/timeout): %s, retrying...", e)
                time.sleep(5)
            except KeyboardInterrupt:
                # Allow graceful shutdown
                raise
            except Exception as e:
                logger.exception("Unexpected error processing message: %s", e)
                time.sleep(5)

        logger.info("Worker stopped")


def main() -> None:
    """Worker entry point with health server.

    Starts Flask health server in background thread, then runs
    SQS polling worker in main thread (blocks until shutdown signal).
    """
    try:
        logger.info("Initializing NRF Impact Assessment Worker...")

        config = WorkerConfig()
        logger.info(
            "Configuration loaded: region=%s, endpoint=%s, queue_name=%s",
            config.region,
            config.endpoint_url,
            config.sqs_queue_name,
        )

        # Before starting health server, check if port is available
        if not is_port_available(config.health_port):
            logger.error("Health port %s is not available", config.health_port)
            sys.exit(1)

        sqs_client = boto3.client(
            "sqs",
            region_name=config.region,
            endpoint_url=config.endpoint_url,
        )
        logger.info("SQS client initialized")

        # Look up queue URL from queue name (CDP pattern)
        logger.info("Looking up queue URL for: %s", config.sqs_queue_name)
        queue_url_response = sqs_client.get_queue_url(QueueName=config.sqs_queue_name)
        sqs_queue_url = queue_url_response["QueueUrl"]
        logger.info("Queue URL resolved: %s", sqs_queue_url)

        health_thread = threading.Thread(
            target=run_health_server,
            args=(config.health_port,),
            daemon=True,
            name="health-server",
        )
        health_thread.start()
        logger.info("Health check server started in background thread")

        worker = Worker(sqs_client=sqs_client, sqs_queue_url=sqs_queue_url)
        worker.run()

    except Exception:
        logger.exception("Worker failed to start")
        sys.exit(1)


if __name__ == "__main__":
    main()
