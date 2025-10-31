# Worker integration plan

**Date:** 2025-10-30
**Objective:** Migrate NRF Impact Assessment Worker functionality into this CDP-compliant service codebase

## Context

We have two codebases:

1. **nrf-impact-assessment-service** (this repo) - FastAPI template from CDP, currently minimal
2. **nrf-impact-assessment-worker** (separate repo) - Production-ready worker with full assessment logic

**Goal:** Replace the FastAPI app with the worker functionality while maintaining CDP platform compliance (health endpoint).

## Strategy

### Preserve the worker, add minimal HTTP

The worker is our primary concern. We only need HTTP for platform compliance:

- **Primary function**: Long-running SQS polling worker
- **Secondary function**: HTTP `/health` endpoint for CDP monitoring
- **Architecture**: Flask-in-daemon-thread pattern (see `cdp-adaptation.md`)

### Incremental migration approach

Rather than a big-bang migration, we'll build incrementally:

**Phase 1: Hello world worker** ✓ (this phase)
- Create minimal worker structure
- Simple SQS message logger
- Flask health endpoint
- Prove the pattern works

**Phase 2: Copy core domain**
- Port worker code from other repo
- models/, calculators/, repositories/
- Keep tests alongside

**Phase 3: Add dependencies**
- PostgreSQL/PostGIS in compose.yml
- S3 integration
- Full AWS config

**Phase 4: Full integration**
- Complete processor logic
- Email/financial services
- Production configuration

## Phase 1: Hello world worker (current)

### What we're building

```
worker/
├── __init__.py
├── health.py          # Flask health endpoint (from cdp-adaptation.md)
├── worker.py          # Main worker loop + entry point
└── config.py          # Basic AWS/SQS config
```

### Key files

**worker/health.py** - Flask app with Waitress WSGI server for `/health` endpoint

**worker/worker.py** - Main worker with:
- SQS polling loop (receives messages, logs them, deletes them)
- Signal handling (SIGTERM/SIGINT for graceful shutdown)
- Flask health server started in daemon thread
- Worker loop runs in main thread

**worker/config.py** - Configuration using Pydantic settings:
- AWS region and endpoint URL (for LocalStack)
- SQS queue URL
- Health server port

### Infrastructure changes

**pyproject.toml** - Add minimal dependencies: boto3, flask, waitress, pydantic-settings

**Dockerfile** - Change CMD to: `["-m", "worker.worker"]`

**compose.yml** - Keep LocalStack for SQS, remove MongoDB for now

### Success criteria

- ✅ Worker starts without errors
- ✅ Health endpoint responds on `/health`
- ✅ Worker polls SQS every 20 seconds
- ✅ Messages are received, logged, and deleted
- ✅ Graceful shutdown on SIGTERM/SIGINT
- ✅ End-to-end test passes

### End-to-end test

Create automated test that verifies the complete flow:

**tests/e2e/test_worker_integration.py** - Docker-based integration test:
- Uses pytest with docker-compose fixture
- Starts LocalStack + worker service
- Creates SQS queue
- Sends test message
- Polls docker logs for expected output
- Verifies message was deleted from queue
- Checks health endpoint responds

Run with: `uv run pytest tests/e2e/ -v`

This gives confidence the whole system works before proceeding to phase 2.

## Phase 2: Copy core domain (next steps)

Once phase 1 works, we'll:

1. **Copy worker code from other repo** - All modules: models/, calculators/, repositories/, services/, validation/, aws/, utils/, tests/

2. **Update dependencies** - Add: geopandas, sqlalchemy, geoalchemy2, psycopg2-binary, alembic, email-validator

3. **Replace hello world with real processor** - Import AssessmentJobProcessor, initialize PostGIS repository, process real jobs

4. **Add PostgreSQL to compose.yml** - Use postgis/postgis:16-3.4 image (matching worker repo)

5. **Add database migrations** - Copy alembic/ directory

6. **Run tests to verify** - All 90 tests should pass

## Phase 3: Add full AWS integration

- S3 bucket configuration
- Update compose/aws.env with S3 settings
- Add LocalStack S3 initialization script
- Test full S3 → SQS → Worker → Assessment flow

## Phase 4: Production readiness

- Correlation ID tracing (see cdp-adaptation.md notes)
- Enhanced health metrics (optional)
- CloudWatch logging configuration
- Production environment variables
- Documentation updates

## References

- `docs/cdp-adaptation.md` - Architectural decision for Flask-in-thread pattern
- `nrf-impact-assessment-worker/README.md` - Original worker documentation
- `nrf-impact-assessment-worker/worker/worker.py` - Reference implementation

## Notes

### Why not keep FastAPI?

The CDP template uses FastAPI, but we don't need it:

- **Worker is primary concern**: SQS polling, not HTTP requests
- **FastAPI is overkill**: We only need `/health` endpoint
- **Sync vs async**: Worker uses synchronous boto3/SQLAlchemy, no need for async complexity
- **Flask is simpler**: ~30 lines for health endpoint vs full FastAPI app

### Why Flask in thread vs FastAPI lifespan?

Both would work, but Flask-in-thread is simpler for this use case:

- Worker remains synchronous (no async/await needed)
- Clear separation: HTTP in thread, worker in main
- Worker crash = process exits = ECS restarts
- No async event loop management needed

### Next steps after phase 1

Wait for phase 1 to work before proceeding. This ensures:
- Pattern is validated
- Infrastructure is correct
- Team understands the approach
- Can incrementally add complexity

---

**Status**: Phase 1 in progress
**Last updated**: 2025-10-30
