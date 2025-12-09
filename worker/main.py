"""Main entrypoint for multi-process worker application."""

from __future__ import annotations

import logging
import multiprocessing
import signal
import sys
import time

import boto3

from worker.config import WorkerConfig
from worker.health import run_health_server
from worker.state import WorkerState, create_shared_state
from worker.worker import Worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global references for signal handling
shutdown_event: multiprocessing.Event | None = None
worker_process: multiprocessing.Process | None = None
health_process: multiprocessing.Process | None = None


def signal_handler(signum: int, frame) -> None:  # noqa: ARG001
    """Handle SIGTERM and SIGINT for graceful shutdown.

    Args:
        signum: Signal number
        frame: Current stack frame (unused)
    """
    logger.info("Received signal %s, initiating shutdown...", signum)
    if shutdown_event:
        shutdown_event.set()


def run_worker_process(
    state: WorkerState, config: WorkerConfig, shutdown_event: multiprocessing.Event
) -> None:
    """Worker process entry point.

    Initializes SQS client and worker, then runs until shutdown event is set.

    Args:
        state: Shared state for health reporting
        config: Application configuration
        shutdown_event: Signal to stop processing
    """
    # Configure logging for worker process
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - [WORKER] - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("Worker process initializing...")

    try:
        # Initialize SQS client
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

        # Create worker with shared state
        worker = Worker(
            sqs_client=sqs_client,
            sqs_queue_url=sqs_queue_url,
            state=state,
            wait_time_seconds=config.sqs_wait_time_seconds,
        )

        # Check shutdown event periodically
        # Worker.run() handles its own loop, so we just need to monitor shutdown
        # and call stop() when signaled
        def check_shutdown():
            while not shutdown_event.is_set():
                time.sleep(1)
            logger.info("Shutdown event detected, stopping worker...")
            worker.stop()

        # Start shutdown checker in a daemon thread
        import threading

        shutdown_thread = threading.Thread(target=check_shutdown, daemon=True)
        shutdown_thread.start()

        # Run worker (blocks until worker.running becomes False)
        worker.run()

    except Exception as e:
        logger.exception("Worker process failed: %s", e)
        state.status_flag.value = -1  # Mark as errored
        raise


def main() -> None:
    """Main entry point with multi-process orchestration.

    Creates shared state, starts health server and worker processes,
    handles signals, and coordinates graceful shutdown.
    """
    global shutdown_event, worker_process, health_process

    logger.info("Starting NRF Impact Assessment Worker (multi-process)")

    # Load configuration
    config = WorkerConfig()
    logger.info(
        "Configuration loaded: region=%s, endpoint=%s, queue_name=%s, port=%s",
        config.region,
        config.endpoint_url,
        config.sqs_queue_name,
        config.health_port,
    )

    # Create shared state for IPC
    state = create_shared_state()
    shutdown_event = multiprocessing.Event()

    # Install signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        # Start health server process
        health_process = multiprocessing.Process(
            target=run_health_server,
            args=(state, config.health_port),
            name="health-server",
            daemon=False,  # Want explicit cleanup
        )
        health_process.start()
        logger.info("Health server process started (PID: %s)", health_process.pid)

        # Start worker process
        worker_process = multiprocessing.Process(
            target=run_worker_process,
            args=(state, config, shutdown_event),
            name="sqs-worker",
            daemon=False,
        )
        worker_process.start()
        logger.info("Worker process started (PID: %s)", worker_process.pid)

        # Wait for shutdown signal
        while not shutdown_event.is_set():
            time.sleep(1)

            # Check if worker crashed
            if not worker_process.is_alive():
                logger.error("Worker process died unexpectedly")
                break

        logger.info("Shutdown signal received, stopping processes...")

        # Graceful worker shutdown
        if worker_process.is_alive():
            logger.info("Waiting for worker to finish (30s timeout)...")
            worker_process.join(timeout=30)
            if worker_process.is_alive():
                logger.warning("Worker did not stop gracefully, terminating...")
                worker_process.terminate()
                worker_process.join(timeout=5)

        # Stop health server
        if health_process.is_alive():
            logger.info("Stopping health server...")
            health_process.terminate()
            health_process.join(timeout=5)

        logger.info("All processes stopped, exiting")
        sys.exit(0)

    except Exception as e:
        logger.exception("Fatal error in main process: %s", e)

        # Emergency cleanup
        if worker_process and worker_process.is_alive():
            worker_process.terminate()
        if health_process and health_process.is_alive():
            health_process.terminate()

        sys.exit(1)


if __name__ == "__main__":
    main()
