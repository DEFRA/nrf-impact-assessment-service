# NRF Impact assessment worker - architecture

**Date:** 2025-12-09
**Service Type:** CPU-intensive SQS consumer with HTTP health endpoint

> **⚠️ IMPORTANT NOTE:**
> The timeout values, health check thresholds, and retry configurations documented here are **initial estimates** based on what we've seen with two initial impact assessments in non production envs.
> These have **not been validated** and should be tuned based on actual metrics and performance data once load/capacity testing is done.

---

## Overview

The NRF Impact Assessment Worker is a long-running ECS task that:
1. Polls SQS queue for nutrient impact assessment requests
2. Performs CPU-intensive geospatial processing (geopandas, GDAL, shapely)
3. Sends notifications on completion
4. Exposes HTTP `/health` endpoint for CDP platform monitoring

**Key architectural decision:** Multiprocessing with separate health server process to ensure reliable health monitoring during CPU-intensive work.

---

## Process architecture

```
┌────────────────────────────────────────┐
│         Main Process (Worker)          │
│  - Signal handlers (SIGTERM/SIGINT)    │
│  - Creates shared state                │
│  - Spawns health server child process  │
│  - SQS polling                         │
│  - Heavy geo work                      │
│  - Updates:                            │
│    • status                            │
│    • heartbeat                         │
│    • task timing                       │
└────────────────────────────────────────┘
           │
           │ (spawns)
           ▼
┌──────────────────┐
│ Health Process   │
│                  │
│ - Flask+Waitress │
│ - /health route  │
│ - Reads shared   │
│   state only     │
│                  │
└──────────────────┘
```

**Two-process design:** Main process runs the worker directly and spawns a single child process for the health server. This is simpler and more efficient than a three-process orchestrator pattern.

### Why multiprocessing?

| Approach | Result |
|----------|--------|
| **Threading** | Health endpoint becomes unresponsive during CPU work due to Python GIL |
| **Multiprocessing** | ✅ Health server always responsive, can detect worker hangs/crashes |

---

## Message processing strategy

### Current: Single message per loop

The worker processes **one message per loop iteration** (`MaxNumberOfMessages=1`).

### Considered alternative: batch processing

SQS supports receiving up to 10 messages per call. We explicitly chose **not** to batch for the following reasons:

#### Trade-offs

| Aspect | Single Message | Batch Processing (10) |
|--------|---------------|----------------------|
| **SQS API costs** | Higher (more calls) | ✅ Lower (fewer calls) |
| **Throughput** | Limited by processing time | ✅ Higher potential throughput |
| **Heartbeat updates** | ✅ Frequent (every loop) | Gaps during batch processing |
| **Health monitoring** | ✅ Accurate (short gaps) | Risk of false negatives |
| **Failure isolation** | ✅ One failure = one retry | Partial batch failures complex |
| **Observability** | ✅ Simple (one message = one log flow) | Interleaved logs harder to trace |
| **Latency consistency** | ✅ Predictable | One slow message blocks batch |

#### Recommendation: scale horizontally

For CPU-intensive geospatial workloads, **scaling out (more worker instances)** is preferred over **scaling up (batching)**:

**Benefits of horizontal scaling:**
- ✅ **Better isolation**: One message failure doesn't affect others
- ✅ **Consistent heartbeats**: Frequent updates enable reliable health checks
- ✅ **Linear scaling**: Add instances to handle load spikes
- ✅ **Simpler code**: No partial batch failure handling
- ✅ **Better observability**: One message per log flow

**When to reconsider batching:**
- Queue has very high message volume (thousands per minute)
- Processing time is uniform and short (<10 seconds per message)
- SQS API costs are a significant budget concern
- Can implement adaptive timeout per batch (complex)

**Implementation note:** If batching is needed in future, consider:
1. Make `MaxNumberOfMessages` configurable (env var)
2. Update heartbeat between messages in batch
3. Add batch-level metrics and logging
4. Handle partial batch failures gracefully

---

## Health check design

### Endpoint

**`GET /health`**

Returns:
- **200 OK**: Worker running and heartbeat fresh
- **503 Service Unavailable**: Worker stopped, errored, or stale

### Health determination logic

```python
healthy = (status == 1) and (heartbeat_age < timeout)
```

Where:
- `status`: `-1` (error), `0` (stopped), `1` (running)
- `heartbeat_age`: Current time - last heartbeat timestamp
- `timeout`: Configurable via `HEARTBEAT_TIMEOUT` (default: 180s)

### Adaptive timeout

The health check supports adaptive timeouts for long-running tasks. The shared state includes:
- `task_start_time`: When long task began (0 if idle)
- `expected_task_duration`: Estimated duration (0 if idle)

Health server uses task-aware timeout:
```python
if task_start > 0 and expected_duration > 0:
    # Long task in progress
    effective_timeout = expected_duration * TASK_TIMEOUT_BUFFER
    is_overtime = task_elapsed > effective_timeout
else:
    # Normal operation
    effective_timeout = DEFAULT_HEARTBEAT_TIMEOUT
    is_overtime = heartbeat_age > effective_timeout
```

**Current usage:** While the adaptive timeout logic is implemented, the worker does not yet set `task_start_time` or `expected_task_duration`, so the default fixed timeout (180s) is used for all operations. This will be utilized when Phase 4 (business logic) implements long-running assessment processing.

---

## Configuration

Most configuration is hardcoded with sensible defaults. Optional environment variable overrides:

| Variable | Default | Description |
|----------|---------|-------------|
| `SQS_ENDPOINT` | _(AWS default)_ | SQS endpoint (LocalStack only) |
| `SQS_WAIT_TIME_SECONDS` | `20` | Long polling wait (max: 20) |
| `HEALTH_PORT` | `8085` | Health endpoint port |
| `HEARTBEAT_TIMEOUT` | `120` | Health check timeout (seconds) |
| `TASK_TIMEOUT_BUFFER` | `1.5` | Adaptive timeout multiplier |

**Hardcoded values:**
- Region: `eu-west-2` (all CDP environments)
- Queue name: `nrf_impact_assessment_queue`

### Tuning Guidelines

> **Note:** These are initial estimates and should be adjusted based on production metrics.

**`SQS_WAIT_TIME_SECONDS`:**
- Production: `20` (maximum, most efficient)
- Development: `5-10` (faster shutdown for testing)

**`HEARTBEAT_TIMEOUT`:**
- Current: `120s` (2 minutes) - reduced from 180s due to retry logic
- Base on P95 processing time once measured
- Too low: False positives (ECS restarts healthy tasks)
- Too high: Slow hang detection

---

## Error handling

### Error classification

**Fatal errors** (mark as error, re-raise):
- `QueueDoesNotExist`: Queue not found or deleted
- `InvalidAccessKeyId`: AWS credentials invalid
- `InvalidClientTokenId`: AWS security token invalid
- `SignatureDoesNotMatch`: Request signature verification failed
- `AccessDenied`: Insufficient IAM permissions

**Note:** If using an encrypted queue, add `KmsAccessDenied`, `KmsDisabled`, `KmsNotFound` to fatal errors list.

**Transient errors** (log, retry in 5s):
- Network timeouts (`BotoCoreError`)
- Throttling (`RequestThrottled`)
- All other `ClientError` codes not in fatal list

**Unexpected errors** (log with traceback, retry in 5s):
- Any other exception type

### Error handling behavior

- **Fatal errors**: Mark worker as failed (status=-1) and terminate
- **Transient errors**: Log warning, sleep 5s, retry
- **Unexpected errors**: Log with full traceback, sleep 5s, retry
- **KeyboardInterrupt**: Re-raised for graceful shutdown (local development only)

This approach keeps the worker resilient to temporary issues while failing fast on unrecoverable configuration or authentication problems.

See `worker/worker.py:_handle_client_error()` for implementation.

---

## Shutdown sequence

1. **Signal received** (SIGTERM/SIGINT) → Signal handler calls `worker.stop()`
2. **Main process (worker)** stops polling loop → Sets status=0 → Finishes current message
3. **Main process** exits → Health server child process terminated via context manager
4. **Clean exit**

**Design goal:** Allow in-progress message to complete before terminating.

---

## Scaling considerations

### Horizontal scaling

**Metrics to monitor:**
- Queue depth (`ApproximateNumberOfMessages`)
- Message age (`ApproximateAgeOfOldestMessage`)
- Worker CPU utilization

**Auto-scaling rule example:**
```yaml
# Scale out when queue depth > 100 messages
# Scale in when queue depth < 20 messages and CPU < 50%
```

**Benefits:**
- Linear scaling with predictable behavior
- Failure isolation (one instance crash doesn't affect others)
- Rolling deployments without processing interruption

### Vertical scaling

Increasing instance size (more CPU/RAM) has limited value because:
- Each message still processes sequentially
- Doesn't reduce queue depth faster
- More expensive than horizontal scaling
- No redundancy (single point of failure)

---

## References

- [AWS SQS Best Practices](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-best-practices.html)
