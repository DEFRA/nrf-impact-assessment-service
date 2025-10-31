"""Health check endpoint for CDP platform compliance."""

import logging

from flask import Flask, jsonify
from waitress import serve

logger = logging.getLogger(__name__)

# Simple health status for Phase 1
# CDP platform sidecars already monitor task health, so we keep this minimal
app = Flask(__name__)


@app.route("/health")
def health():
    """Health check endpoint.

    Returns 200 OK if worker is running.
    CDP platform requires this endpoint for ECS health checks.
    """
    return jsonify({"status": "ok"}), 200


def run_health_server(port: int = 8085) -> None:
    """Run WSGI server for health checks.

    Uses Waitress instead of Flask's development server to handle
    concurrent health check requests without blocking.

    Args:
        port: Port to listen on (default: 8085)
    """
    logger.info("Starting health check server (Waitress) on port %s", port)
    serve(app, host="0.0.0.0", port=port, threads=4)  # noqa: S104
