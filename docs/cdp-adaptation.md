# CDP platform adaptation for worker service

**Date:** 2025-10-27
**Context:** Adapting the NRF Impact Assessment Worker to meet CDP platform requirements while maintaining worker architecture

## Problem statement

The NRF Impact Assessment Worker is a long-running ECS task that polls SQS messages, processes nutrient impact assessments, and sends notifications.
It is **not** a request/response API service.

However, the DEFRA CDP platform requires all services to expose an HTTP `/health` endpoint for:
- ECS health checks
- Service monitoring and alerting
- Platform compliance

The platform team's standard pattern uses FastAPI for web services, which we believe isn't a good match for a worker service.

## Requirements

1. **Primary function**: Long-running SQS polling worker (main concern)
2. **Secondary function**: HTTP `/health` endpoint for platform monitoring
3. **Platform compliance**: Must expose endpoint on configured PORT (default 8085)
4. **ECS compatibility**: Health checks via `http://localhost:8085/health`
5. **Minimal overhead**: Health endpoint should not interfere with worker performance

## Architecture decision

**Use Flask in a background thread for health monitoring, with the worker running in the main thread.**

### Why Flask in thread?

| Approach                     | Pros | Cons | Verdict           |
|------------------------------|------|------|-------------------|
| **Flask in daemon thread**   | Simple, minimal, worker unchanged, lightweight | Threading complexity (minimal) | ✅ **Recommended** |
| FastAPI                      | Matches platform patterns, modern async | Heavy framework for one endpoint, requires async worker | ❌ Overkill        |
| Starlette                    | Lighter than FastAPI | Still heavier than Flask, requires async | ❌ Unnecessary     |

### Key decision factors

1. **Worker is the primary concern**: The assessment processing logic should remain simple, synchronous, and unaffected by HTTP concerns

2. **Health endpoint is ancillary**: It exists for platform compliance, not as a core feature. Should be minimal.

3. **Synchronous worker**: Current worker uses synchronous boto3 for SQS. No need to introduce async complexity.

4. **Thread safety**: Simple shared dict for detailed health state would sufficient, if needed

5. **Failure semantics**: If worker crashes, process exits and ECS restarts it. Daemon thread ensures health server dies with worker.

## Implementation pattern

### Health server module

Create a separate module for the health endpoint to keep concerns separated:

```python
# worker/health.py
"""Health check endpoint for CDP platform compliance."""
import logging
from datetime import datetime

from flask import Flask, jsonify
from waitress import serve

logger = logging.getLogger(__name__)

# NOTE: The CDP platform deploys metric sidecars that monitor ECS tasks
# and publish CloudWatch metrics automatically. This shared dict approach
# is proposed to fulfill the /health interface, but may be unnecessary:
# - Platform already monitors task health independently
# - Simple "ok" response meets compliance requirements
# - Making this truly meaningful requires deeper health checks (SQS connectivity,
#   processing pipeline status, etc.) which adds complexity
# - Current proposal is "toylike" - tracks basic counters but doesn't verify
#   actual worker health (can process messages, access dependencies, etc.)
#
# Suggestion: Keep it minimal (just return {"status": "ok"}) unless there's
# a specific need for application-level health reporting beyond platform monitoring.

# If something more than a dumb status OK required, could use shared
# dict for health monitoring.
worker_status = {
  "status": "ok",
  "service": "nrf-impact-assessment-worker",
  "messages_processed": 0,
  "errors": 0,
  "last_job_processed": None
}

# Flask app for health endpoint
app = Flask(__name__)


@app.route('/health')
def health():
    # is updating worker status, then do this
    status_code = 200
    if worker_status["status"] != "ok":
        status_code = 500
    return jsonify(worker_status), status_code

    # otherwise do this
    return jsonify({"status", "ok"})



def run_health_server(port=8085):
    """Run WSGI server for health checks.

    Uses Waitress instead of Flask's development server to handle
    concurrent health check requests without blocking.
    """
    logger.info(f"Starting health check server (Waitress) on port {port}")
    serve(app, host='0.0.0.0', port=port, threads=4)
```

### Worker integration

Modify `worker/worker.py` to integrate the health server:

```python
# worker/worker.py (changes only)

# Add imports
import threading
from datetime import datetime, timezone
from worker.health import run_health_server, worker_status

# In Worker.run() method, add state tracking:
# NOTE: Given that CDP platform sidecars already monitor task health,
# this status tracking may be unnecessary overhead. The worker could simply
# run without updating worker_status, and the /health endpoint would just
# return {"status": "ok"}. The tracking shown below only adds value if:
# - There's a specific operational need to see message counts/errors via /health
# - There is a need for application-level observability beyond platform metrics
# Otherwise, consider omitting this tracking entirely.

def run(self) -> None:
    """Main polling loop."""
    logger.info("Worker started, polling for jobs...")

    while self.running:
        try:
            results = self.sqs_client.receive_messages()
            # ... existing message processing ...

            worker_status["status"] = "ok"
            worker_status["messages_processed"] += 1
            worker_status["last_message_processed"] = datetime.now(timezone.utc)

        except Exception as e:
            worker_status["status"] = "internal server error"
            worker_status["errors"] += 1
            logger.exception(f"Unexpected error in worker loop: {e}")

# In main() function, add health server thread before worker.run():
def main():
    """Worker entry point with health server."""
    try:
        # ... existing initialization ...

        processor = AssessmentJobProcessor(...)

        # Start health server in background thread
        health_thread = threading.Thread(
            target=run_health_server,
            args=(worker_config.health_port,),
            daemon=True,
            name="health-server"
        )
        health_thread.start()
        logger.info("Health check server started in background thread")

        # Run worker in main thread (blocks until shutdown signal)
        worker = Worker(sqs_client=sqs_client, processor=processor)
        worker.run()

    except Exception as e:
        logger.exception(f"Worker failed to start: {e}")
        worker_status["status"] = "internal server error"
        sys.exit(1)
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

CDP platform requires a single `/health` endpoint (following the reference implementation in `nrf-impact-assessment-service`).

### `/health` - Health check
Returns worker health status:

**Minimal version (CDP compliant):**
```json
{
  "status": "ok"
}
```

**Enhanced version with metrics (optional):**
```json
{
  "status": "ok",
  "service": "nrf-impact-assessment-worker",
  "messages_processed": 0,
  "errors": 0,
  "last_job_processed": None
}
```

## Why not FastAPI?

FastAPI is mismatched here:

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

## Trade-offs and limitations

### Accepted trade-offs
- **Threading**: Adds minimal complexity, but Python's GIL ensures thread safety (for now - at least until 3.14 which isn't that far away ) for simple dict operations
- **Not "pure" worker**: Includes HTTP concerns, but isolated to separate thread

### Limitations
- Health server is basic (no TLS, auth, etc.) - acceptable for internal ECS health checks?
- If "smarter" metrics needed then status reporting would be via shared state dict - fine for simple metrics, would need locks for complex state

## Benefits

1. **Simplicity**: ~50 lines of Flask code, minimal dependencies
2. **Separation of concerns**: Worker logic untouched by HTTP concerns
3. **Platform compliance**: Meets CDP `/health` requirement
4. **Observable**: Health endpoint provides worker state visibility
5. **Maintainable**: Clear pattern, easy to understand
6. **Performant**: No async overhead, no framework bloat

## Future enhancements

If needed, could add:
- Enhanced health metrics (worker already tracks state, could expose more detail in response)
- SQS queue availability checks

## Conclusion

For a worker service that needs minimal HTTP compliance, **Flask in a background thread is sufficient and appropriate**. It provides the required `/health` endpoint without compromising the simplicity and synchronous nature of the worker architecture.

The pattern is:
- ✅ Platform compliant
- ✅ Architecturally sound
- ✅ Simple to implement and maintain
- ✅ Low overhead
- ✅ Observable

FastAPI would be the right choice for a request/response API service, but not for a long-running worker with ancillary health monitoring needs.


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
