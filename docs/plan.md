# Production Migration Plan

**Date:** 2025-12-10
**Status:** Multiprocess health check architecture complete
**Objective:** Migrate NRF Impact Assessment production functionality into this service

---

## Context

**This repository (nrf-impact-assessment-service)** is the production application with:
- ✅ Multiprocess architecture (main worker process + health server child process)
- ✅ CDP platform compliance (HTTP `/health` endpoint)
- ✅ SQS polling infrastructure
- ✅ Production-grade error handling and shutdown

**Source repository (nrf-impact-assessment-worker)** contains:
- Production impact assessment logic (geopandas, GDAL, shapely)
- Database models and repositories (PostgreSQL/PostGIS)
- Business calculators and validation
- Test suite
- **Alembic migrations** (needs converting to Liquibase for CDP)

**Goal:** Migrate production functionality piece by piece into this repo.

---

## High-level migration

### Phase 1: Core infrastructure ✅ COMPLETE
- Multiprocess worker architecture
- Health check endpoint with heartbeat monitoring
- SQS message polling (single message pattern)
- Error handling and graceful shutdown
- Integration tests with LocalStack

### Phase 2: Database layer (next)
- Add PostgreSQL/PostGIS to `compose.yml` for local development
- Copy database models from source repo
- Copy SQLAlchemy repositories
- **Convert Alembic migrations to Liquibase** (CDP requirement)
- Add database dependencies to `pyproject.toml`
- Verify connection and basic CRUD operations

### Phase 3: Integration with nrf-backend (new work)
**Note:** This phase is new - not implemented in spike repo. The upstream architecture was finalized after the initial exploration.

**Processing flow:**
1. Receive SQS message (trigger with assessment reference)
2. HTTP GET assessment details from nrf-backend (geometry + request data)
3. Process assessment (business logic)
4. HTTP POST results back to nrf-backend
5. Send email notification

**Implementation tasks:**
- Implement HTTP client for communicating with nrf-backend service
- GET assessment details endpoint (retrieves geometry and request data)
- POST assessment results endpoint (sends back outcome)
- Handle large geometries via HTTP (avoid SQS message size limits)
- Wire up processing flow in `_process_message()`

### Phase 4: Business logic
- Copy assessment calculators from source repo
- Copy validation logic
- Copy domain models
- Implement full assessment processing workflow
- Replace TODO in `_process_message()` with actual logic

### Phase 5: Notification services
- Copy email/notification services from source repo
- Add notification dependencies
- Wire up to assessment completion
- Test notification delivery

### Phase 6 (Optional): S3 Integration
- S3 file upload/download capability (for future user file upload journey)
- Currently no user journey requires this
- Defer until frontend adds file upload functionality

### Phase 7: Test coverage
- Copy/adapt test suite from source repo
- Adapt tests for new architecture
- Add integration tests for multiprocess behavior

---

## Important: Database migrations

**CDP Platform uses Liquibase (not Alembic) for schema migrations.**

### Migration requirements

The source repo uses Alembic for database migrations. CDP requires Liquibase with specific constraints:

- Changelog must be in XML format (named `db.changelog.xml`)
- Stored in repository at `./changelog/db.changelog.xml`
- Published via GitHub Actions (see CDP docs)
- Applied via CDP Portal interface

### Phase 2 approach

1. Copy database models from source repo (keep as SQLAlchemy)
2. Convert Alembic migrations to Liquibase XML format
3. Set up `./changelog/db.changelog.xml` structure
4. Add GitHub workflow for publishing migrations (`.github/workflows/publish-db-schema.yml`)
5. Test migrations locally with Liquibase CLI before publishing

### References

- [CDP Relational Databases Guide](https://defra-digital-dev.github.io/cdp-user-guide/applications/relational-databases.html)
- [Liquibase Documentation](https://docs.liquibase.com/home.html)
- [CDP Example Repository](https://github.com/DEFRA/cdp-example-node-postgres-be) - See `changelog/` directory

---

## Current architecture

See `docs/architecture.md` for complete details.

**Key points:**
- 2-process design: main process runs worker, spawns health server child
- Shared state via `multiprocessing.Value` for IPC
- Single message per loop (horizontal scaling pattern)
- Fatal errors terminate, transient errors retry

---

## Migration strategy

**Incremental approach:**
1. Complete one phase at a time
2. Verify functionality works before proceeding
3. Keep `main` branch deployable throughout
4. Test each integration point

**Key principle:** Don't break existing health check/worker infrastructure while adding functionality.

---

## Next steps (Phase 2)

1. **Verify CDP database details** - PostgreSQL/PostGIS already provisioned (version TBD)
2. Add PostgreSQL/PostGIS to `compose.yml` for local dev (match CDP version when confirmed)
3. Copy `models/` directory from source repo
4. Copy `repositories/` directory from source repo
5. **Convert Alembic migrations to Liquibase XML**
6. Create `./changelog/db.changelog.xml` with converted migrations
7. Add SQLAlchemy, GeoAlchemy2, psycopg2-binary to dependencies
8. Set up GitHub workflow for publishing Liquibase migrations
9. Create simple test that connects to database and runs a query
10. Test migrations locally before publishing

---

## References

- `docs/architecture.md` - Current multiprocess architecture
- Source repo: nrf-impact-assessment-worker (non-public)
- CDP Platform docs: https://defra-digital-dev.github.io/cdp-user-guide/

---

**Last Updated:** 2025-12-10
