"""NRF Impact Assessment Worker - SQS polling with health endpoint."""

import logging
import time
from typing import Any

import botocore.exceptions
from mypy_boto3_sqs import SQSClient
from pydantic import BaseModel, Field

from worker.state import WorkerState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

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

    def run(self) -> None:
        """Main polling loop with heartbeat updates for health monitoring.

        Updates shared state to signal liveness to health server process:
        - Sets status to 1 (running) on startup
        - Updates heartbeat timestamp after each loop iteration
        - Sets status to -1 on unrecoverable error
        - Sets status to 0 on clean shutdown
        """
        logger.info("Worker process started, polling for messages...")
        logger.info("Queue URL: %s", self.sqs_queue_url)

        if self.state:
            self.state.status_flag.value = 1  # Running
            self.state.last_heartbeat.value = time.time()
            logger.info("Worker state initialized: status=running")

        try:
            while self.running:
                try:
                    if self.state:
                        self.state.last_heartbeat.value = time.time()

                    # Single message per loop - see docs/architecture.md for rationale
                    # (prefer horizontal scaling over batch processing)
                    response = self.sqs_client.receive_message(
                        QueueUrl=self.sqs_queue_url,
                        MaxNumberOfMessages=1,
                        WaitTimeSeconds=self.wait_time_seconds,
                    )

                    # AWS returns Messages as a list, extract single message if present
                    messages = response.get("Messages", [])
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
                    raise

                except botocore.exceptions.ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "")
                    self._handle_client_error(error_code)

                except botocore.exceptions.BotoCoreError as e:
                    logger.warning(
                        "AWS SDK error (network/timeout): %s, retrying in 5s...", e
                    )
                    time.sleep(5)

                except Exception as e:
                    logger.exception("Unexpected error processing message: %s", e)
                    logger.info("Retrying in 5s...")
                    time.sleep(5)

        finally:
            logger.info("Worker shutting down")
            if self.state:
                self.state.status_flag.value = 0
                logger.info("Worker state updated: status=stopped")

    def stop(self) -> None:
        """Request worker to stop gracefully.

        Called by signal handlers or shutdown coordinator.
        """
        logger.info("Stop requested, shutting down gracefully...")
        self.running = False

    def _handle_client_error(self, error_code: str) -> None:
        """Handle AWS ClientError based on error code.

        Args:
            error_code: AWS error code from the exception

        Raises:
            Exception: Re-raises for fatal errors (queue not found, auth failures)
        """
        # Fatal errors - cannot recover, mark as error and re-raise
        fatal_errors = {
            "QueueDoesNotExist": "Queue does not exist or was deleted",
            "InvalidAccessKeyId": "AWS credentials invalid",
            "SignatureDoesNotMatch": "Request signature verification failed",
            "AccessDenied": "Insufficient IAM permissions",
        }

        if error_code in fatal_errors:
            logger.error("%s: %s", fatal_errors[error_code], error_code)
            if self.state:
                self.state.status_flag.value = -1  # Mark as error
            raise

        # Transient errors - log and retry
        logger.warning("AWS client error: %s, retrying in 5s...", error_code)
        time.sleep(5)

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

    def _process_message(self, message: SQSMessage) -> None:
        """Process a single SQS message.

        Args:
            message: Validated SQS message to process
        """
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
