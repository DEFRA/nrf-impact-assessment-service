"""Shared state for inter-process communication between worker and health server."""

from dataclasses import dataclass
from multiprocessing import Value


@dataclass
class WorkerState:
    """Shared state between worker and health server processes.

    Uses multiprocessing.Value for atomic cross-process access.
    Thread-safe and process-safe read/write operations.

    Attributes:
        status_flag: Worker status (-1 = error, 0 = stopped, 1 = running)
        last_heartbeat: Epoch timestamp of last heartbeat update
        task_start_time: Epoch timestamp when long task began (0 if idle)
        expected_task_duration: Expected duration in seconds for current task (0 if idle)
    """

    # Core status: -1 = error, 0 = stopped, 1 = running
    status_flag: Value

    # Heartbeat tracking (epoch seconds)
    last_heartbeat: Value

    # Long-task tracking for adaptive timeout (optional, for future enhancement)
    task_start_time: Value
    expected_task_duration: Value


def create_shared_state() -> WorkerState:
    """Initialize shared state for worker and health server.

    Creates multiprocessing.Value objects that can be safely shared
    between processes with atomic read/write operations.

    Returns:
        WorkerState with initialized multiprocessing.Value objects
    """
    return WorkerState(
        status_flag=Value("i", 0),  # Start as stopped
        last_heartbeat=Value("d", 0.0),
        task_start_time=Value("d", 0.0),
        expected_task_duration=Value("d", 0.0),
    )
