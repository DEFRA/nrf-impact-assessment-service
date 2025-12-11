"""NRF Impact Assessment Worker - SQS polling with health endpoint."""

import logging
import time
from typing import Any

import botocore.exceptions
from mypy_boto3_sqs import SQSClient
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from worker.state import WorkerState, WorkerStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# A set of AWS error codes that are considered transient and safe to retry.
# Any ClientError with a code NOT in this set will be treated as fatal.
TRANSIENT_ERROR_CODES = {
    "Throttling",
    "ThrottlingException",
    "ProvisionedThroughputExceededException",
    "ServiceUnavailable",
    "InternalFailure",
    "InternalError",
    "OverLimit",
}


def is_transient_aws_error(exception: BaseException) -> bool:
    """Return True if the exception is a transient AWS error safe for retry."""
    if isinstance(exception, botocore.exceptions.ClientError):
        error_code = exception.response.get("Error", {}).get("Code")
        if error_code in TRANSIENT_ERROR_CODES:
            logger.debug(
                "Identified transient ClientError (code: %s), will retry.", error_code
            )
            return True
    if isinstance(exception, botocore.exceptions.BotoCoreError):
        logger.debug("Identified BotoCoreError, will retry: %s", exception)
        return True
    logger.debug("Exception is not a transient AWS error: %s", type(exception).__name__)
    return False


# Default expected duration for task processing (seconds)
# Conservative estimate for CPU-intensive geospatial work
# Tune based on actual P95 processing times in production
DEFAULT_TASK_DURATION = 300.0  # 5 minutes


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


class Worker:
    """SQS polling worker with graceful shutdown and health reporting.

    Integrates with shared state for multiprocess health monitoring.
    Updates heartbeat regularly to signal liveness to health server process.
    """

    def __init__(
        self,
        sqs_client: SQSClient,
        sqs_queue_url: str,
        state: WorkerState | None = None,
        wait_time_seconds: int = 20,
    ):
        """Initialize worker.

        Args:
            sqs_client: Boto3 SQS client
            sqs_queue_url: SQS queue URL to poll
            state: Shared state for health reporting (optional, for multiprocess mode)
            wait_time_seconds: SQS long polling wait time in seconds (max: 20)
        """
        self.sqs_client = sqs_client
        self.sqs_queue_url = sqs_queue_url
        self.state = state
        self.wait_time_seconds = wait_time_seconds
        self.running = True

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(5),
        retry=retry_if_exception(is_transient_aws_error),
        before_sleep=lambda retry_state: logger.warning(
            "Retrying SQS poll due to %s (attempt %s)...",
            retry_state.outcome.exception().__class__.__name__,
            retry_state.attempt_number,
        ),
    )
    def _receive_messages(self) -> list[dict[str, Any]]:
        """Receive messages from SQS, with retries on transient errors."""
        response = self.sqs_client.receive_message(
            QueueUrl=self.sqs_queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=self.wait_time_seconds,
        )
        return response.get("Messages", [])

    def run(self) -> None:
        """Main polling loop with heartbeat updates for health monitoring."""
        logger.info("Worker process started, polling for messages...")
        logger.info("Queue URL: %s", self.sqs_queue_url)

        if self.state:
            self.state.status_flag.value = WorkerStatus.RUNNING
            self.state.last_heartbeat.value = time.time()
            logger.info("Worker state initialized: status=running")

        try:
            while self.running:
                if self.state:
                    self.state.last_heartbeat.value = time.time()

                messages = self._receive_messages()

                # Mark as ready after first successful poll
                if self.state and not self.state.ready.value:
                    self.state.ready.value = 1
                    logger.info("Worker ready: successfully connected to SQS")

                if messages:
                    message = SQSMessage(**messages[0])
                    self._process_message(message)
                else:
                    logger.debug(
                        "No messages received (%ss poll timeout)",
                        self.wait_time_seconds,
                    )

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, shutting down...")

        except Exception as e:
            # Catches fatal errors or if tenacity gives up retrying.
            logger.exception("Fatal error in worker loop, shutting down: %s", e)
            if self.state:
                self.state.status_flag.value = WorkerStatus.ERROR

        finally:
            logger.info("Worker shutting down")
            if self.state:
                self.state.status_flag.value = WorkerStatus.STOPPED
                logger.info("Worker state updated: status=stopped")

    def stop(self) -> None:
        """Request worker to stop gracefully."""
        logger.info("Stop requested, shutting down gracefully...")
        self.running = False

    def _delete_message(self, message_id: str, receipt_handle: str) -> None:
        """Delete a message from the queue with error handling."""
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

    def _process_message(self, message: SQSMessage) -> None:
        """Process a single SQS message."""
        message_id = message.message_id
        body = message.body
        receipt_handle = message.receipt_handle

        logger.info("Received message: %s", message_id)
        logger.info("Message body: %s", body)

        # Set task timing for adaptive timeout during processing
        if self.state:
            self.state.task_start_time.value = time.time()
            self.state.expected_task_duration.value = DEFAULT_TASK_DURATION

        try:
            # TODO: Add actual impact assessment processing logic here
            pass
        finally:
            # Clear task timing and update heartbeat after processing completes
            if self.state:
                self.state.task_start_time.value = 0.0
                self.state.expected_task_duration.value = 0.0
                self.state.last_heartbeat.value = time.time()

        self._delete_message(message_id, receipt_handle)
