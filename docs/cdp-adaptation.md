# CDP platform adaptation for worker service

**Date:** 2025-12-09
**Context:** Adapting the NRF Impact Assessment Worker to meet CDP platform requirements while maintaining worker architecture

## Problem statement

The NRF Impact Assessment Worker is a long-running ECS task that polls SQS messages, processes CPU- and I/O-intensive nutrient impact assessments using geospatial libraries, and sends notifications.
It is **not** a request/response API service.

However, the DEFRA CDP platform requires all services to expose an HTTP `/health` endpoint for:
- ECS health checks
- Service monitoring and alerting
- Platform compliance

The platform team's standard pattern uses FastAPI for web services, which we believe isn't a good match for a worker service.

## Requirements

1. **Primary function**: Long-running SQS polling worker with CPU-intensive geospatial processing (main concern)
2. **Secondary function**: HTTP `/health` endpoint for platform monitoring
3. **Platform compliance**: Must expose endpoint on configured PORT (default 8085)
4. **ECS compatibility**: Health checks via `http://localhost:8085/health`
5. **Minimal overhead**: Health endpoint should not interfere with worker performance
6. **Reliability**: Health endpoint must remain responsive even during intensive processing
7. **Accurate health reporting**: Must detect worker hangs, crashes, and deadlocks

## Architecture decision

**Use multiprocessing with separate worker and health server processes, coordinated via shared state.**

### Why multiprocessing over threading?

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Multiprocessing** | True process isolation, bypasses GIL, health server always responsive, can detect worker hangs/crashes via heartbeat, accurate health semantics | More complex IPC, requires shared state management | ✅ **Required** |
| Flask in daemon thread | Simple, minimal code | GIL contention during CPU-intensive work, health endpoint can become unresponsive, cannot reliably detect worker hangs, shared fate (daemon dies with worker) | ❌ Insufficient |
| FastAPI | Matches platform patterns, modern async | Heavy framework for one endpoint, requires async worker refactor, still doesn't solve GIL problem for CPU-bound work | ❌ Overkill |

### Key decision factors

1. **CPU-intensive geospatial processing**: The worker performs heavy computation with geopandas, GDAL, and shapely. Python's GIL means a background thread would be starved during these operations, making health checks unresponsive or slow.

2. **True health monitoring**: A separate process can detect when the worker has hung, deadlocked, or crashed by monitoring heartbeat timestamps. A thread in the same process cannot reliably detect these conditions.

3. **Process isolation**: The health server process runs independently and remains responsive regardless of worker CPU load. This ensures ECS health checks never time out due to worker activity.

4. **Worker is the primary concern**: The assessment processing logic remains simple, synchronous, and unaffected by HTTP concerns (same as threading approach).

5. **Accurate health semantics**: Health endpoint reflects actual worker functionality (processing messages, updating heartbeat) rather than just "process exists".

6. **Platform compliance requirement**: ECS will kill tasks that fail health checks. Unreliable health reporting causes cascading failures during legitimate long-running operations.

## Implementation pattern

### Overview: Multi-process architecture

```
┌─────────────────────────────────────────┐
│         Main Process                    │
│  - Signal handlers (SIGTERM/SIGINT)    │
│  - Creates shared state                │
│  - Starts child processes               │
│  - Coordinates shutdown                 │
└─────────────────────────────────────────┘
           │                    │
           ▼                    ▼
┌──────────────────┐   ┌──────────────────┐
│ Worker Process   │   │ Health Process   │
│                  │   │                  │
│ - SQS polling    │   │ - Flask+Waitress │
│ - Heavy geo work │◄──┤ - /health route  │
│ - Updates:       │   │ - Reads shared   │
│   • status       │   │   state only     │
│   • heartbeat    │   │                  │
│   • task timing  │   │                  │
└──────────────────┘   └──────────────────┘
```

### 1. Shared state

Create `worker/shared_state.py` to define IPC schema:

```python
"""Shared state for inter-process communication between worker and health server."""
from multiprocessing import Value
from typing import NamedTuple


class WorkerState(NamedTuple):
    """Shared state between worker and health server processes.

    Uses multiprocessing.Value for atomic cross-process access.
    """
    # Core status: -1 = error, 0 = stopped, 1 = running
    status_flag: Value  # Value('i')

    # Heartbeat tracking (epoch seconds)
    last_heartbeat: Value  # Value('d')

    # Long-task tracking for adaptive timeout
    task_start_time: Value  # Value('d')  # 0 if idle
    expected_task_duration: Value  # Value('d')  # 0 if idle


def create_shared_state() -> WorkerState:
    """Initialize shared state for worker and health server.

    Returns:
        WorkerState with initialized multiprocessing.Value objects
    """
    return WorkerState(
        status_flag=Value('i', 0),  # Start as stopped
        last_heartbeat=Value('d', 0.0),
        task_start_time=Value('d', 0.0),
        expected_task_duration=Value('d', 0.0),
    )
```

### 2. Health server process

Create `worker/health_server.py`:

```python
"""Health check HTTP server process for CDP platform compliance."""
import logging
import os
import time
from multiprocessing import Event

from flask import Flask, jsonify
from waitress import serve

from worker.state import WorkerState

logger = logging.getLogger(__name__)

# Health check configuration from environment
DEFAULT_HEARTBEAT_TIMEOUT = int(os.getenv('HEARTBEAT_TIMEOUT', '180'))  # seconds
TASK_TIMEOUT_BUFFER = float(os.getenv('TASK_TIMEOUT_BUFFER', '1.5'))  # Allow 50% extra time for long tasks


def create_health_app(state: WorkerState) -> Flask:
    """Create Flask app with access to shared worker state.

    Args:
        state: Shared state from worker process

    Returns:
        Configured Flask application
    """
    app = Flask(__name__)

    @app.route('/health')
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
        heartbeat_age = now - last_heartbeat if last_heartbeat > 0 else float('inf')

        # Determine effective timeout (adaptive for long tasks)
        if task_start > 0 and expected_duration > 0:
            # Long task in progress: use task-aware timeout
            task_elapsed = now - task_start
            effective_timeout = expected_duration * TASK_TIMEOUT_BUFFER
            is_overtime = task_elapsed > effective_timeout

            logger.debug(
                f"Long task check: elapsed={task_elapsed:.1f}s, "
                f"expected={expected_duration:.1f}s, "
                f"timeout={effective_timeout:.1f}s, "
                f"overtime={is_overtime}"
            )
        else:
            # Normal operation: use default timeout
            effective_timeout = DEFAULT_HEARTBEAT_TIMEOUT
            is_overtime = heartbeat_age > effective_timeout

            logger.debug(
                f"Normal check: heartbeat_age={heartbeat_age:.1f}s, "
                f"timeout={effective_timeout:.1f}s, "
                f"overtime={is_overtime}"
            )

        # Determine health
        healthy = (status == 1) and not is_overtime

        if healthy:
            return jsonify({
                "status": "ok",
                "service": "nrf-impact-assessment-worker",
                "heartbeat_age": round(heartbeat_age, 2)
            }), 200
        else:
            reason = []
            if status != 1:
                reason.append(f"status={status}")
            if is_overtime:
                reason.append(f"heartbeat_stale ({heartbeat_age:.1f}s > {effective_timeout:.1f}s)")

            return jsonify({
                "status": "unavailable",
                "service": "nrf-impact-assessment-worker",
                "reason": ", ".join(reason)
            }), 503

    return app


def run_health_server(state: WorkerState, port: int, shutdown_event: Event) -> None:
    """Run health check server in separate process.

    Args:
        state: Shared state from worker process
        port: TCP port to listen on
        shutdown_event: Signal to stop server
    """
    logger.info(f"Health server process starting on port {port}")

    app = create_health_app(state)

    # Waitress for production-grade WSGI serving
    # Note: shutdown_event could be used for graceful shutdown if needed,
    # but typically the health server is killed when main process exits
    logger.info(f"Health server ready on http://0.0.0.0:{port}/health")
    serve(app, host='0.0.0.0', port=port, threads=4)
```

### 3. Worker process integration

Modify `worker/worker.py` to update shared state:

```python
# worker/worker.py

import time
from worker.state import WorkerState


class Worker:
    def __init__(
            self,
            sqs_client,
            processor,
            state: WorkerState | None = None
    ):
        self.sqs_client = sqs_client
        self.processor = processor
        self.state = state  # Shared state for health reporting
        self.running = True

    def run(self) -> None:
        """Main polling loop with heartbeat updates."""
        logger.info("Worker process started, polling for messages...")

        if self.state:
            self.state.status_flag.value = 1  # Mark as running
            self.state.last_heartbeat.value = time.time()

        try:
            while self.running:
                try:
                    # Update heartbeat at loop start
                    if self.state:
                        self.state.last_heartbeat.value = time.time()

                    # Poll SQS
                    results = self.sqs_client.receive_messages()

                    if not results:
                        time.sleep(1)  # Brief pause if no messages
                        continue

                    for message in results:
                        self._process_message(message)

                except Exception as e:
                    logger.exception(f"Error in worker loop: {e}")
                    if self.state:
                        self.state.status_flag.value = -1  # Mark as errored
                    time.sleep(5)  # Back off on error

        finally:
            logger.info("Worker shutting down")
            if self.state:
                self.state.status_flag.value = 0  # Mark as stopped

    def _process_message(self, message) -> None:
        """Process a single SQS message with long-task tracking."""
        message_id = message.get('MessageId', 'unknown')
        logger.info(f"Processing message {message_id}")

        # Extract assessment data
        body = json.loads(message['Body'])

        # Estimate processing time (example: based on geometry complexity)
        estimated_duration = self._estimate_duration(body)

        # Signal long task start
        if self.state and estimated_duration > 30:
            self.state.task_start_time.value = time.time()
            self.state.expected_task_duration.value = estimated_duration
            logger.debug(f"Long task started: {estimated_duration}s estimated")

        try:
            # Perform heavy geospatial processing
            self.processor.process(body)

            # Delete from SQS on success
            self.sqs_client.delete_message(message)
            logger.info(f"Message {message_id} processed successfully")

        finally:
            # Clear long task state
            if self.state:
                self.state.task_start_time.value = 0.0
                self.state.expected_task_duration.value = 0.0
                self.state.last_heartbeat.value = time.time()

    def _estimate_duration(self, body: dict) -> float:
        """Estimate processing duration based on input complexity.

        Args:
            body: SQS message body

        Returns:
            Estimated duration in seconds
        """
        # Example heuristic - adjust based on actual workload
        # Could look at geometry size, number of features, etc.
        return 45.0  # Default estimate for geospatial work
```

### 4. Main process orchestration

Create/modify `worker/main.py`:

```python
"""Main entrypoint for multi-process worker application."""
import logging
import multiprocessing
import signal
import sys
import time

from worker.state import create_shared_state
from worker.health import run_health_server
from worker.worker import Worker
from worker.config import load_config

logger = logging.getLogger(__name__)

# Global shutdown event for signal handling
shutdown_event = None
worker_process = None
health_process = None


def signal_handler(signum, frame):
    """Handle SIGTERM and SIGINT for graceful shutdown."""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    if shutdown_event:
        shutdown_event.set()


def main():
    """Main entry point with multi-process orchestration."""
    global shutdown_event, worker_process, health_process

    # Configure logging for main process
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger.info("Starting NRF Impact Assessment Worker (multi-process)")

    # Load configuration
    config = load_config()

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
            args=(state, config.health_port, shutdown_event),
            name="health-server",
            daemon=False  # Want explicit cleanup
        )
        health_process.start()
        logger.info(f"Health server process started (PID: {health_process.pid})")

        # Start worker process
        # Note: Worker initialization happens in the worker process
        worker_process = multiprocessing.Process(
            target=run_worker_process,
            args=(state, config, shutdown_event),
            name="sqs-worker",
            daemon=False
        )
        worker_process.start()
        logger.info(f"Worker process started (PID: {worker_process.pid})")

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
        logger.exception(f"Fatal error in main process: {e}")

        # Emergency cleanup
        if worker_process and worker_process.is_alive():
            worker_process.terminate()
        if health_process and health_process.is_alive():
            health_process.terminate()

        sys.exit(1)


def run_worker_process(state: WorkerState, config, shutdown_event):
    """Worker process entry point.

    Args:
        state: Shared state for health reporting
        config: Application configuration
        shutdown_event: Signal to stop processing
    """
    # Configure logging for worker process
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - [WORKER] - %(name)s - %(levelname)s - %(message)s'
    )

    logger.info("Worker process initializing...")

    try:
        # Initialize SQS client and processor
        sqs_client = create_sqs_client(config)
        processor = create_processor(config)

        # Create worker with shared state
        worker = Worker(
            sqs_client=sqs_client,
            processor=processor,
            state=state
        )

        # Run until shutdown
        worker.run()

    except Exception as e:
        logger.exception(f"Worker process failed: {e}")
        state.status_flag.value = -1  # Mark as errored
        raise


if __name__ == '__main__':
    main()
```

### Why Waitress for the health server?

**Flask's built-in server (`app.run()`) is single-threaded by default**, which creates a risk:

**The problem:**
- Health checks can arrive while previous check is still processing
- Single threaded server = requests queue up and block
- If health check response exceeds timeout, ECS marks task as unhealthy
- **Risk:** Cascading failure - ECS restarts task, causing more health check failures

**Why Waitress:**

1. **Production ready**: Designed for production use, unlike Flask's development server
   ```python
   # Flask dev server (single-threaded)
   app.run()  # ❌ NOT for production

   # Waitress (multi-threaded)
   serve(app, threads=4)  # ✅
   ```

2. **Concurrent request handling**: Multiple health checks can be processed simultaneously without blocking

3. **Cross-platform**: Pure Python - no other dependencies

4. **Lightweight**: Minimal overhead compared to full ASGI servers

5. **Simple**: Drop-in replacement for `app.run()` with better reliability

**Dependencies:**
```toml
[project]
dependencies = [
    "flask>=3.0.0",
    "waitress>=3.0.0",  # Production WSGI server
]
```

## Health endpoint design

CDP platform requires a single `/health` endpoint. The multiprocessing implementation provides **accurate health semantics** based on worker state.

### `/health` - Health check

**Healthy response (200 OK):**
```json
{
  "status": "ok",
  "service": "nrf-impact-assessment-worker",
  "heartbeat_age": 2.5
}
```

Returned when:
- `status_flag == 1` (worker running)
- Heartbeat is fresh (within adaptive timeout)

**Unhealthy response (503 Service Unavailable):**
```json
{
  "status": "unavailable",
  "service": "nrf-impact-assessment-worker",
  "reason": "heartbeat_stale (125.3s > 60.0s)"
}
```

Returned when:
- `status_flag == 0` (stopped) or `-1` (error)
- Heartbeat exceeds timeout (worker hung or deadlocked)
- Long task exceeds 1.5x expected duration

## Why not FastAPI?

FastAPI is not a great here:

1. **BackgroundTasks are per-request**: Designed for short tasks after HTTP responses, not long-running workers
   ```python
   # This pattern doesn't fit
   @app.post("/endpoint")
   async def handler(background_tasks: BackgroundTasks):
       background_tasks.add_task(some_quick_task)  # Runs after response
   ```

2. **Lifespan events work but add complexity**: Would require converting worker to async
   ```python
   # Possible but unnecessary
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       worker_task = asyncio.create_task(async_worker_loop())
       yield
       worker_task.cancel()
   ```

3. **Framework overhead**: FastAPI includes routing, validation, OpenAPI, serialization - all unused for a single `/health` endpoint

4. **Async complexity**: Using FastAPI forces you to deal with async/await throughout. You have two bad options:

   a) **Go async all the way**: Convert the entire worker to async patterns
      - Replace `boto3` with `aioboto3`
      - Add `async`/`await` to all worker functions
      - Manage async context managers and event loops
      - Significant refactor for no functional benefit

   b) **Use thread executor**: Wrap blocking `boto3` calls to avoid blocking the event loop
      ```python
      loop = asyncio.get_event_loop()
      messages = await loop.run_in_executor(None, sqs_client.receive_message)
      ```
      - Adds complexity and indirection throughout the worker
      - Still running sync code, just with async wrapper boilerplate

   **Either way, FastAPI becomes the center of the architecture** for a service that has no HTTP requirements beyond a health check.

## Configuration

The health check behavior is configurable via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `HEARTBEAT_TIMEOUT` | `180` | Seconds before worker is considered stale. Set to cover your longest typical geospatial operation. |
| `TASK_TIMEOUT_BUFFER` | `1.5` | Multiplier for adaptive timeout (e.g., 1.5 = allow 50% extra time for long tasks). |
| `PORT` | `8085` | TCP port for health endpoint (CDP platform standard). |

**Example ECS task definition:**
```json
{
  "environment": [
    {"name": "HEARTBEAT_TIMEOUT", "value": "240"},
    {"name": "TASK_TIMEOUT_BUFFER", "value": "2.0"},
    {"name": "PORT", "value": "8085"}
  ]
}
```

### Choosing the right timeout value

**For geospatial operations taking 2-3 minutes:**
- Set `HEARTBEAT_TIMEOUT=180` (3 minutes) or `HEARTBEAT_TIMEOUT=240` (4 minutes)
- This allows operations to complete without triggering false positive unhealthy status
- Downside: Takes 3-4 minutes to detect genuine hangs/deadlocks

**Trade-off:**
- Higher timeout = more tolerant of long operations, slower hang detection
- Lower timeout = faster hang detection, risk of false positives during legitimate work

**Monitoring strategy:**
Start with `180` (3 min) and monitor CloudWatch logs. If you see frequent unhealthy checks during normal operation, increase it. If you want faster hang detection for shorter operations, consider implementing the adaptive timeout strategy (see Future Enhancements).

## Adaptive timeout strategy (optional)

The implementation includes **task-aware health checking** support for mixed workloads (some quick, some slow operations):

### The Problem
Fixed heartbeat timeout causes either:
- False positives (timeout too short for long operations)
- Slow hang detection (timeout too long for quick operations)

### The Solution (optional enhancement)
Track task metadata in shared state to apply different timeouts:
- `task_start_time`: When current long task began (0 if idle)
- `expected_task_duration`: Estimated duration in seconds (0 if idle)

### Worker behavior
```python
# Before long task
if estimated_duration > 30:
    state.task_start_time.value = time.time()
    state.expected_task_duration.value = estimated_duration

# After task completes
state.task_start_time.value = 0.0
state.expected_task_duration.value = 0.0
state.last_heartbeat.value = time.time()
```

### Health server logic
```python
if task_start > 0 and expected_duration > 0:
    # Long task in progress: use task-specific timeout with buffer
    task_elapsed = now - task_start
    effective_timeout = expected_duration * TASK_TIMEOUT_BUFFER
    is_overtime = task_elapsed > effective_timeout
else:
    # Normal operation: use default timeout
    is_overtime = heartbeat_age > DEFAULT_HEARTBEAT_TIMEOUT
```

**When to implement this:**
- You have mixed workload (some messages take 10s, others take 180s)
- You want fast hang detection for quick operations
- You're willing to implement task duration estimation logic

**When to skip this:**
- Most operations take similar time (2-3 minutes)
- Simple fixed timeout (180s) works fine
- Don't want complexity of duration estimation

For initial implementation, **use fixed timeout (Option 1)**. Add adaptive timeout later if needed.

## Trade-offs and limitations

### Accepted trade-offs
- **IPC complexity**: Multiprocessing adds complexity over threading, but provides true isolation and reliability
- **Not "pure" worker**: Includes HTTP concerns, but isolated to separate process
- **Shared state**: Uses `multiprocessing.Value` for IPC - simple but requires understanding of shared memory semantics

### Limitations
- Health server is basic (no TLS, auth, etc.) - acceptable for internal ECS health checks
- Task duration estimation required for adaptive timeout - needs calibration based on actual workload
- Memory shared between processes - ensure no large objects in shared state

## Benefits

1. **Reliability**: Health endpoint always responsive, regardless of worker CPU load
2. **True health monitoring**: Can detect worker hangs, crashes, and deadlocks
3. **Platform compliance**: Meets CDP `/health` requirement with production-grade implementation
4. **Adaptive timeouts**: Prevents false positives during legitimate long-running operations
5. **Process isolation**: Bypasses Python GIL limitations for CPU-intensive work
6. **Observable**: Health endpoint provides accurate worker state visibility
7. **Maintainable**: Clear pattern with well-defined responsibilities
8. **Separation of concerns**: Worker logic untouched by HTTP concerns

## Implementation checklist

- [ ] Create `worker/shared_state.py` with `WorkerState` and factory function
- [ ] Create `worker/health_server.py` with Flask app and adaptive timeout logic
- [ ] Modify `worker/worker.py` to accept `WorkerState` and update heartbeat
- [ ] Add long-task tracking (set `task_start_time` and `expected_task_duration`)
- [ ] Create/modify `worker/main.py` with multiprocessing orchestration
- [ ] Install signal handlers (SIGTERM/SIGINT)
- [ ] Test health endpoint responds during idle state
- [ ] Test health endpoint responds during CPU-intensive processing
- [ ] Test health endpoint detects worker hang (stop updating heartbeat)
- [ ] Test graceful shutdown on SIGTERM
- [ ] Calibrate `DEFAULT_HEARTBEAT_TIMEOUT` based on typical message processing
- [ ] Calibrate task duration estimates based on actual workload

## Future enhancements

If needed, could add:
- **Additional metrics**: Track consecutive SQS errors, last successful poll time via `multiprocessing.Manager().dict()`
- **Intra-task heartbeats**: For very long tasks (>10 minutes), update heartbeat at processing checkpoints
- **SQS queue availability checks**: Proactive health degradation if queue is inaccessible

## Conclusion

For a CPU-intensive SQS worker with geospatial processing, **multiprocessing with separate health server process is a sound architectural choice**. It provides:

- ✅ Platform compliance
- ✅ Architecturally sound for CPU-intensive work
- ✅ Reliable health monitoring with crash/hang detection
- ✅ Always-responsive health endpoint (bypasses GIL)
- ✅ Accurate health semantics (not just "process exists")
- ✅ Adaptive timeout strategy for long-running operations
- ✅ Production-grade implementation

FastAPI would be the right choice for a request/response API service. Threading would be acceptable for I/O-bound workers with light processing. But for CPU-intensive geospatial processing with reliability requirements, **multiprocessing is necessary**.


# Notes regarding correlation id tracing for worker service

## Context

The CDP python backend template that we would be adapting to implements trace ID middleware for HTTP requests via `TraceIdMiddleware` which extracts the `x-cdp-request-id` header and stores it in a `ContextVar` for distributed tracing across services.

When adapting to a worker architecture (processing SQS messages instead of HTTP requests), we need a similar correlation tracking mechanism.

## Approach: ContextVars + logging filter

**HTTP Service:** Extract from `x-cdp-request-id` header → Store in ContextVar
**Worker Service:** Extract from SQS message attribute (`CorrelationId`) → Store in ContextVar

Both patterns enable following a single request across multiple services.

## Why ContextVars with logging filter?

**Without filter:** Must pass `correlation_id` to every function and add `extra={"correlation_id": ...}` to every log statement. Tedious and error-prone.

**With filter:** Set context once at message start. All subsequent log statements automatically include correlation_id without explicit parameter passing or `extra=` dictionaries.

## Implementation

**1. Context variables** (`worker/tracing.py`):
- `ctx_correlation_id` - Stores correlation ID from SQS message attribute
- `ctx_message` - Stores message metadata (ID, queue URL)
- `set_message_context(message)` - Extracts and sets context at message start

**2. Logging filter** (`worker/tracing.py`):
- `CorrelationIdFilter` - Automatically injects `correlation_id` from context into log records
- Handles missing correlation ID gracefully (sets to `None`)

**3. Logging configuration** (application startup):
- Add `CorrelationIdFilter` to logging handler
- Format string includes `%(correlation_id)s` placeholder
- Example: `'%(asctime)s - %(name)s - [%(correlation_id)s] - %(message)s'`

**4. Usage pattern** (worker loop):
```python
def process_message(message):
    set_message_context(message)  # Set once
    logger.info("Processing message")  # correlation_id auto-included
    # ... all logging throughout call stack includes correlation_id
```

## Benefits

- **Distributed tracing**: Follow requests across Front end → SQS → Worker → downstream services
- **Automatic injection**: No manual parameter passing or `extra=` dictionaries
- **Consistent pattern**: Same approach as HTTP middleware, adapted for messaging
- **Simple maintenance**: Add logging anywhere in call stack without tracking correlation ID
