# NRF Impact assessment worker - architecture

**Date:** 2025-12-09
**Service Type:** CPU-intensive SQS consumer with HTTP health endpoint

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

### Why multiprocessing?

| Approach | Result |
|----------|--------|
| **Threading** | Health endpoint becomes unresponsive during CPU work due to Python GIL |
| **Multiprocessing** | ✅ Health server always responsive, can detect worker hangs/crashes |

See [cdp-adaptation.md](./cdp-adaptation.md) for full rationale.

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

### Adaptive timeout (optional)

For mixed workloads with varying processing times, the shared state includes:
- `task_start_time`: When long task began (0 if idle)
- `expected_task_duration`: Estimated duration (0 if idle)

Health server can then use task-aware timeout:
```python
if task_in_progress:
    effective_timeout = expected_duration * TASK_TIMEOUT_BUFFER
else:
    effective_timeout = DEFAULT_HEARTBEAT_TIMEOUT
```

**Current implementation:** Uses simple fixed timeout (180s) for all operations. Adaptive timeout available for future enhancement if needed.

---

## Configuration

All behavior configurable via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_SQS_QUEUE_NAME` | `nrf_impact_assessment_queue` | SQS queue name |
| `AWS_SQS_WAIT_TIME_SECONDS` | `20` | Long polling wait (max: 20) |
| `AWS_HEALTH_PORT` | `8085` | Health endpoint port |
| `HEARTBEAT_TIMEOUT` | `180` | Health check timeout (seconds) |
| `TASK_TIMEOUT_BUFFER` | `1.5` | Adaptive timeout multiplier |

### Tuning Guidelines

**`AWS_SQS_WAIT_TIME_SECONDS`:**
- Production: `20` (maximum, most efficient)
- Development: `5-10` (faster shutdown for testing)

**`HEARTBEAT_TIMEOUT`:**
- Base on P95 processing time
- Example: If 95% of messages process in 2 minutes, set to `180` (3 minutes)
- Too low: False positives (ECS restarts healthy tasks)
- Too high: Slow hang detection

---

## Error handling

### Error classification

**Fatal errors** (mark as error, re-raise):
- `QueueDoesNotExist`: Queue configuration issue
- `AccessDenied`: IAM permissions issue
- `InvalidAccessKeyId`: Credentials issue
- `SignatureDoesNotMatch`: AWS auth issue

**Transient errors** (log, retry in 5s):
- Network timeouts (`BotoCoreError`)
- Throttling errors
- Temporary service issues

**Unexpected errors** (log with traceback, retry in 5s):
- Processing errors (logged but worker keeps running)
- Transient issues should not crash the worker

### Error flow

```
Exception raised
    ↓
Is it KeyboardInterrupt? → Yes → Log, re-raise (graceful shutdown)
    ↓ No
Is it ClientError? → Yes → Check error code
    ↓                        ↓
    |                  Fatal? → Yes → Mark status=-1, re-raise
    |                        ↓ No
    |                  Transient → Log, sleep 5s, continue
    ↓ No
Is it BotoCoreError? → Yes → Log "network/timeout", sleep 5s, continue
    ↓ No
Generic Exception → Log with traceback, sleep 5s, continue
```

See `worker/worker.py:_handle_client_error()` for implementation.

---

## Shutdown sequence

1. **Signal received** (SIGTERM/SIGINT) → Main process sets `shutdown_event`
2. **Worker process** detects event → Stops polling loop → Sets status=0
3. **Main process** waits up to 30s for worker to finish current message
4. **Timeout fallback** → Terminate worker process
5. **Health server** terminated
6. **Main process** exits cleanly

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

- [CDP Platform Adaptation](./cdp-adaptation.md) - Multiprocessing health check design rationale
- [AWS SQS Best Practices](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-best-practices.html)
