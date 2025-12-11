"""Shared state for inter-process communication between worker and health server."""

from dataclasses import dataclass
from enum import IntEnum
from multiprocessing import Value


class WorkerStatus(IntEnum):
    """Worker status codes for shared state."""

    ERROR = -1
    STOPPED = 0
    RUNNING = 1


@dataclass
class WorkerState:
    """Shared state between worker and health server processes.

    Uses multiprocessing.Value for atomic cross-process access.
    Thread-safe and process-safe read/write operations.

    Attributes:
        status_flag: Worker status (use WorkerStatus enum values)
        last_heartbeat: Epoch timestamp of last heartbeat update
        ready: Whether worker has successfully connected to SQS (0=not ready, 1=ready)
        task_start_time: Epoch timestamp when long task began (0 if idle)
        expected_task_duration: Expected duration in seconds for current task (0 if idle)
    """

    # Core status: WorkerStatus enum values
    status_flag: Value

    # Heartbeat tracking (epoch seconds)
    last_heartbeat: Value

    # Readiness flag (0 = not ready, 1 = ready)
    ready: Value

    # Long-task tracking for adaptive timeout
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
        status_flag=Value("i", WorkerStatus.STOPPED),
        last_heartbeat=Value("d", 0.0),
        ready=Value("i", 0),  # Not ready until first successful SQS poll
        task_start_time=Value("d", 0.0),
        expected_task_duration=Value("d", 0.0),
    )
