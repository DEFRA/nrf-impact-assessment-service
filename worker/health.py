"""Health check HTTP server process for CDP platform compliance."""

import logging
import os
import time

from flask import Flask, jsonify
from waitress import serve

from worker.state import WorkerState

logger = logging.getLogger(__name__)

# Health check configuration from environment
DEFAULT_HEARTBEAT_TIMEOUT = int(
    os.getenv("HEARTBEAT_TIMEOUT", "180")
)  # seconds (3 minutes)
TASK_TIMEOUT_BUFFER = float(
    os.getenv("TASK_TIMEOUT_BUFFER", "1.5")
)  # Allow 50% extra time for long tasks


def create_health_app(state: WorkerState) -> Flask:
    """Create Flask app with access to shared worker state.

    Args:
        state: Shared state from worker process

    Returns:
        Configured Flask application
    """
    app = Flask(__name__)

    @app.route("/health")
    def health():
        """Health check endpoint with adaptive timeout logic.

        Returns:
            200 OK if worker is running and heartbeat is fresh
            503 Service Unavailable if worker is stopped, errored, or stale
        """
        now = time.time()

        # Read shared state (thread-safe)
        status = state.status_flag.value
        last_heartbeat = state.last_heartbeat.value
        task_start = state.task_start_time.value
        expected_duration = state.expected_task_duration.value

        # Calculate heartbeat age
        heartbeat_age = now - last_heartbeat if last_heartbeat > 0 else float("inf")

        # Determine effective timeout (adaptive for long tasks)
        if task_start > 0 and expected_duration > 0:
            # Long task in progress: use task-aware timeout
            task_elapsed = now - task_start
            effective_timeout = expected_duration * TASK_TIMEOUT_BUFFER
            is_overtime = task_elapsed > effective_timeout

            logger.debug(
                "Long task check: elapsed=%.1fs, expected=%.1fs, timeout=%.1fs, overtime=%s",
                task_elapsed,
                expected_duration,
                effective_timeout,
                is_overtime,
            )
        else:
            # Normal operation: use default timeout
            effective_timeout = DEFAULT_HEARTBEAT_TIMEOUT
            is_overtime = heartbeat_age > effective_timeout

            logger.debug(
                "Normal check: heartbeat_age=%.1fs, timeout=%.1fs, overtime=%s",
                heartbeat_age,
                effective_timeout,
                is_overtime,
            )

        # Determine health
        healthy = (status == 1) and not is_overtime

        if healthy:
            return (
                jsonify(
                    {
                        "status": "ok",
                        "service": "nrf-impact-assessment-worker",
                        "heartbeat_age": round(heartbeat_age, 2),
                    }
                ),
                200,
            )
        reason = []
        if status != 1:
            reason.append(f"status={status}")
        if is_overtime:
            reason.append(
                f"heartbeat_stale ({heartbeat_age:.1f}s > {effective_timeout:.1f}s)"
            )

        return (
            jsonify(
                {
                    "status": "unavailable",
                    "service": "nrf-impact-assessment-worker",
                    "reason": ", ".join(reason),
                }
            ),
            503,
        )

    return app


def run_health_server(state: WorkerState, port: int) -> None:
    """Run health check server in separate process.

    Args:
        state: Shared state from worker process
        port: TCP port to listen on
    """
    logger.info("Health server process starting on port %s", port)

    app = create_health_app(state)

    # Waitress for production-grade WSGI serving
    # Note: shutdown_event could be used for graceful shutdown if needed,
    # but typically the health server is terminated when main process exits
    logger.info("Health server ready on http://0.0.0.0:%s/health", port)  # noqa: S104
    serve(app, host="0.0.0.0", port=port, threads=4)  # noqa: S104
