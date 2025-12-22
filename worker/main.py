"""Main entrypoint for multi-process worker application."""

from __future__ import annotations

import json
import logging.config
import os
import signal
import sys

import boto3

from worker.config import WorkerConfig
from worker.health import run_health_server
from worker.state import WorkerStatus, create_shared_state
from worker.utils import managed_process
from worker.worker import Worker

# Load logging configuration from JSON file
config_file = os.getenv("LOG_CONFIG", "logging-dev.json")
with open(config_file) as f:
    log_config = json.load(f)
    logging.config.dictConfig(log_config)

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
        "Configuration loaded: queue_name=%s, region=%s, health_port=%s, wait_time=%ss, endpoint_url=%s",
        config.sqs_queue_name,
        config.region,
        config.health_port,
        config.sqs_wait_time_seconds,
        config.endpoint_url or "(default AWS endpoint)",
    )

    # Create shared state for IPC
    state = create_shared_state()

    try:
        # Start health server process using the context manager
        with managed_process(
            target=run_health_server,
            args=(state, config),
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

            logger.info(
                "Looking up queue URL for queue_name=%s in region=%s",
                config.sqs_queue_name,
                config.region,
            )
            try:
                queue_url_response = sqs_client.get_queue_url(
                    QueueName=config.sqs_queue_name
                )
                sqs_queue_url = queue_url_response["QueueUrl"]
                logger.info("Queue URL resolved successfully: %s", sqs_queue_url)
            except Exception as e:
                logger.error(
                    "Failed to get queue URL for queue_name=%s: %s",
                    config.sqs_queue_name,
                    e,
                )
                raise

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
        state.status_flag.value = WorkerStatus.ERROR
        sys.exit(1)

    finally:
        logger.info("Shutdown complete")

    sys.exit(0)


if __name__ == "__main__":
    main()
