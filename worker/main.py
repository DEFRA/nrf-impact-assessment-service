"""Main entrypoint for multi-process worker application."""

from __future__ import annotations

import logging
import signal
import sys

import boto3

from worker.config import WorkerConfig
from worker.health import run_health_server
from worker.state import create_shared_state
from worker.utils import managed_process
from worker.worker import Worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Main entry point - runs worker in main process, health server in child.

    Creates shared state, starts health server process,
    handles signals, and runs worker in main process.
    """
    logger.info("Starting NRF Impact Assessment Worker")

    # Load configuration
    config = WorkerConfig()
    logger.info(
        "Configuration loaded: queue_name=%s, healthcheck port=%s",
        config.sqs_queue_name,
        config.health_port,
    )

    # Create shared state for IPC
    state = create_shared_state()

    try:
        # Start health server process using the context manager
        with managed_process(
            target=run_health_server,
            args=(state, config.health_port),
            name="health-server",
        ) as health_process:
            logger.info("Health server process started (PID: %s)", health_process.pid)

            # Initialize SQS client in main process
            sqs_client = boto3.client(
                "sqs",
                region_name=config.region,
                endpoint_url=config.endpoint_url,
            )
            logger.info("SQS client initialized")

            # Look up queue URL from queue name (CDP pattern)
            logger.info("Looking up queue URL for: %s", config.sqs_queue_name)
            queue_url_response = sqs_client.get_queue_url(
                QueueName=config.sqs_queue_name
            )
            sqs_queue_url = queue_url_response["QueueUrl"]
            logger.info("Queue URL resolved: %s", sqs_queue_url)

            # Create worker with shared state
            worker = Worker(
                sqs_client=sqs_client,
                sqs_queue_url=sqs_queue_url,
                state=state,
                wait_time_seconds=config.sqs_wait_time_seconds,
            )

            # Install signal handlers that call worker.stop()
            signal.signal(signal.SIGTERM, lambda _sig, _frame: worker.stop())
            signal.signal(signal.SIGINT, lambda _sig, _frame: worker.stop())

            logger.info("Starting worker in main process...")
            worker.run()

            logger.info("Worker stopped, cleaning up...")

    except Exception as e:
        logger.exception("Fatal error in main process: %s", e)
        state.status_flag.value = -1  # Mark as errored
        sys.exit(1)

    finally:
        logger.info("Shutdown complete")

    sys.exit(0)

if __name__ == "__main__":
    main()
