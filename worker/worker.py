"""NRF Impact Assessment Worker - SQS polling with health endpoint."""

import logging
import signal
import sys
import threading
import time

import boto3

from worker.config import WorkerConfig
from worker.health import run_health_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class Worker:
    """SQS polling worker with graceful shutdown."""

    def __init__(self, sqs_client, queue_url: str):
        """Initialize worker.

        Args:
            sqs_client: Boto3 SQS client
            queue_url: SQS queue URL to poll
        """
        self.sqs_client = sqs_client
        self.queue_url = queue_url
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

    def run(self) -> None:
        """Main polling loop - receives, logs, and deletes SQS messages."""
        logger.info("Worker started, polling for messages...")
        logger.info("Queue URL: %s", self.queue_url)

        while self.running:
            try:
                # Long polling with 20 second wait time
                response = self.sqs_client.receive_message(
                    QueueUrl=self.queue_url,
                    MaxNumberOfMessages=1,
                    WaitTimeSeconds=20,
                )

                messages = response.get("Messages", [])

                if messages:
                    for message in messages:
                        message_id = message["MessageId"]
                        body = message["Body"]

                        logger.info("Received message: %s", message_id)
                        logger.info("Message body: %s", body)

                        self.sqs_client.delete_message(
                            QueueUrl=self.queue_url,
                            ReceiptHandle=message["ReceiptHandle"],
                        )
                        logger.info("Deleted message: %s", message_id)
                else:
                    logger.debug("No messages received (20s poll timeout)")

            except Exception:
                logger.exception("Error processing message")
                time.sleep(5)  # Brief pause before retrying

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
            "Configuration loaded: region=%s, endpoint=%s",
            config.region,
            config.endpoint_url,
        )

        sqs_client = boto3.client(
            "sqs",
            region_name=config.region,
            endpoint_url=config.endpoint_url,
        )
        logger.info("SQS client initialized")

        health_thread = threading.Thread(
            target=run_health_server,
            args=(config.health_port,),
            daemon=True,
            name="health-server",
        )
        health_thread.start()
        logger.info("Health check server started in background thread")

        worker = Worker(sqs_client=sqs_client, queue_url=config.sqs_queue_url)
        worker.run()

    except Exception:
        logger.exception("Worker failed to start")
        sys.exit(1)


if __name__ == "__main__":
    main()
