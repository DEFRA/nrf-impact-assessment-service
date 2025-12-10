"""Utility functions for the worker application."""

from __future__ import annotations

import multiprocessing
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any


@contextmanager
def managed_process(
    target: Any, args: tuple = (), name: str | None = None
) -> Generator[multiprocessing.Process, None, None]:
    """Context manager to start and gracefully terminate a child process.

    Args:
        target: The callable object to be invoked by the run() method.
        args: The argument tuple for the target invocation.
        name: The process name.

    Yields:
        The started process object.
    """
    process = multiprocessing.Process(
        target=target,
        args=args,
        name=name,
        daemon=False,  # Ensure it's not a daemon to allow for clean shutdown
    )
    try:
        process.start()
        yield process
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join()
