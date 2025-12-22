"""Health check HTTP server process for CDP platform compliance."""

import json
import logging
import logging.config
import os
import time
from typing import Any

from flask import Flask, jsonify
from waitress import serve

from worker.config import WorkerConfig
from worker.state import WorkerState, WorkerStatus

# Load logging configuration from JSON file
# This is needed because health server runs in a separate process
config_file = os.getenv("LOG_CONFIG", "logging-dev.json")
with open(config_file) as f:
    log_config = json.load(f)
    logging.config.dictConfig(log_config)

logger = logging.getLogger(__name__)


def create_health_app(state: WorkerState, config: WorkerConfig) -> Flask:
    """Create Flask app with health endpoint.

    Args:
        state: Shared state from worker process
        config: Worker configuration

    Returns:
        Configured Flask application
    """
    app = Flask(__name__)

    @app.route("/health")
    def health():
        """Health check endpoint with adaptive timeout logic."""
        response_data, status_code = _build_health_response(state, config)
        return jsonify(response_data), status_code

    return app


def run_health_server(state: WorkerState, config: WorkerConfig) -> None:
    """Run health check server in separate process.

    Args:
        state: Shared state from worker process
        config: Worker configuration
    """
    logger.info("Health server process starting on port %s", config.health_port)

    app = create_health_app(state, config)

    # Waitress for WSGI serving
    logger.info("Health server ready on http://0.0.0.0:%s/health", config.health_port)  # noqa: S104
    serve(app, host="0.0.0.0", port=config.health_port, threads=4)  # noqa: S104


def _get_liveness_status(state: WorkerState, config: WorkerConfig) -> dict[str, Any]:
    """Check worker's shared state and return health details.

    Args:
        state: Worker shared state
        config: Worker configuration with timeout settings

    Returns:
        Dictionary with health status and details
    """
    now = time.time()
    status_flag = state.status_flag.value
    last_heartbeat = state.last_heartbeat.value
    task_start = state.task_start_time.value
    expected_duration = state.expected_task_duration.value

    heartbeat_age = now - last_heartbeat if last_heartbeat > 0 else float("inf")

    # Determine effective timeout (adaptive for long tasks)
    is_long_task = task_start > 0 and expected_duration > 0
    if is_long_task:
        effective_timeout = expected_duration * config.task_timeout_buffer
        task_elapsed = now - task_start
        is_overtime = task_elapsed > effective_timeout
    else:
        effective_timeout = config.heartbeat_timeout
        is_overtime = heartbeat_age > effective_timeout

    is_ready = state.ready.value == 1
    is_healthy = (status_flag == WorkerStatus.RUNNING) and not is_overtime

    return {
        "healthy": is_healthy,
        "ready": is_ready,
        "worker_status_flag": status_flag,
        "heartbeat_age_seconds": round(heartbeat_age, 2),
        "is_long_task_running": is_long_task,
        "effective_timeout_seconds": round(effective_timeout, 2),
        "is_overtime": is_overtime,
    }


def _build_health_response(
    state: WorkerState, config: WorkerConfig
) -> tuple[dict[str, Any], int]:
    """Build health check response based on worker state.

    Args:
        state: Worker shared state
        config: Worker configuration

    Returns:
        Tuple of (response_data, status_code)
    """
    liveness = _get_liveness_status(state, config)
    logger.debug("Health check details: %s", liveness)

    if liveness["healthy"]:
        response_data = {
            "status": "ok",
            "service": "nrf-impact-assessment-service",
            "details": {
                "heartbeat_age_seconds": liveness["heartbeat_age_seconds"],
                "is_long_task_running": liveness["is_long_task_running"],
            },
        }
        return response_data, 200

    reason_parts = []
    if liveness["worker_status_flag"] != WorkerStatus.RUNNING:
        reason_parts.append(
            f"worker status is {liveness['worker_status_flag']} "
            f"(expected {WorkerStatus.RUNNING})"
        )
    if liveness["is_overtime"]:
        reason_parts.append("worker is overtime")

    response_data = {
        "status": "unavailable",
        "service": "nrf-impact-assessment-service",
        "reason": ", ".join(reason_parts) or "unknown",
        "details": liveness,
    }
    return response_data, 503
