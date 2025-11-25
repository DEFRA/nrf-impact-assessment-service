# NRF Impact Assessment Service

SQS polling worker for processing nutrient impact assessments, with CDP platform compliance.

**Architecture:** Long-running worker with minimal Flask health endpoint (see [docs/cdp-adaptation.md](docs/cdp-adaptation.md) for rationale)

**Status:** Phase 1 complete - Hello world worker with SQS polling

- [NRF Impact Assessment Service](#nrf-impact-assessment-service)
  - [Requirements](#requirements)
    - [Python](#python)
    - [Linting and Formatting](#linting-and-formatting)
    - [Docker](#docker)
  - [Local development](#local-development)
    - [Setup & Configuration](#setup--configuration)
    - [Running the worker](#running-the-worker)
    - [Testing](#testing)
  - [Health endpoint](#health-endpoint)
  - [Pipelines](#pipelines)
    - [Dependabot](#dependabot)
    - [SonarCloud](#sonarcloud)
  - [Licence](#licence)
    - [About the licence](#about-the-licence)

## Requirements

### Python

Please install python `>= 3.12` and `pipx` in your environment. This template uses [uv](https://github.com/astral-sh/uv) to manage the environment and dependencies.

```python
# install uv via pipx
pipx install uv

# sync dependencies
uv sync

# source python venv
source .venv/bin/activate

# install the pre-commit hooks
pre-commit install
```

### Environment Variable Configuration

The worker uses Pydantic's `BaseSettings` for configuration management in `worker/config.py`, automatically mapping environment variables to configuration fields.

**Key configuration:**
- `AWS_REGION`: AWS region (default: `eu-west-2`)
- `AWS_ENDPOINT_URL`: AWS endpoint (for LocalStack in local dev only)
- `AWS_SQS_QUEUE_NAME`: SQS queue name (default: `nrf_impact_assessment_queue`)

In CDP, environment variables need to be set using CDP conventions:
- [CDP App Config](https://github.com/DEFRA/cdp-documentation/blob/main/how-to/config.md)
- [CDP Secrets](https://github.com/DEFRA/cdp-documentation/blob/main/how-to/secrets.md)

For local development - see [instructions below](#local-development).

### Linting and Formatting

This project uses [Ruff](https://github.com/astral-sh/ruff) for linting and formatting Python code.

#### Running Ruff

To run Ruff from the command line:

```bash
# Run linting with auto-fix
uv run ruff check . --fix

# Run formatting
uv run ruff format .
```

#### Pre-commit Hooks

This project uses [pre-commit](https://pre-commit.com/) to run linting and formatting checks automatically before each commit.

The pre-commit configuration is defined in `.pre-commit-config.yaml`

To set up pre-commit hooks:

```bash
# Set up the git hooks
pre-commit install
```

To run the hooks manually on all files:

```bash
pre-commit run --all-files
```

#### VS Code Configuration

For the best development experience, configure VS Code to use Ruff:

1. Install the [Ruff extension](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff) for VS Code
2. Configure your VS Code settings (`.vscode/settings.json`):

```json
{
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
        "source.fixAll.ruff": "explicit",
        "source.organizeImports.ruff": "explicit"
    },
    "ruff.lint.run": "onSave",
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.formatOnSave": true,
        "editor.codeActionsOnSave": {
            "source.fixAll.ruff": "explicit",
            "source.organizeImports.ruff": "explicit"
        }
    }
}
```

This configuration will:

- Format your code with Ruff when you save a file
- Fix linting issues automatically when possible
- Organize imports according to isort rules

#### Ruff Configuration

Ruff is configured in the `.ruff.toml` file

### Docker

This repository uses Docker throughput its lifecycle i.e. both for local development and the environments. A benefit of this is that environment variables & secrets are managed consistently throughout the lifecycle

See the `Dockerfile` and `compose.yml` for details

## Local development

### Setup & Configuration

Follow the convention below for environment variables and secrets in local development.

**Note** that it does not use `.env` or `python-dotenv` as this is not the convention in the CDP environment.

**Environment variables:** `compose/aws.env`.

**Secrets:** `compose/secrets.env`. You need to create this, as it's excluded from version control.

**Libraries:** Ensure the python virtual environment is configured and libraries are installed using `uv sync`, [as above](#python)

**Pre-Commit Hooks:** Ensure you install the pre-commit hooks, as above

### Running the worker

The worker runs as a Docker container using Docker Compose:

```bash
# Start worker with LocalStack (for local SQS)
docker compose --profile worker up --build

# Watch mode for hot-reloading
# Press 'w' after services start to enable watch mode
```

The worker will:
- Start Flask health server on port 8085
- Poll SQS queue for messages (20 second long-polling)
- Process messages and log results
- Gracefully shutdown on SIGTERM/SIGINT

**Send test message:**

```bash
# Get queue URL (CDP pattern)
QUEUE_URL=$(aws --endpoint-url=http://localhost:4566 sqs get-queue-url \
  --queue-name nrf_impact_assessment_queue \
  --query 'QueueUrl' --output text)

# Send message
aws --endpoint-url=http://localhost:4566 sqs send-message \
  --queue-url "$QUEUE_URL" \
  --message-body '{"test": "hello world"}'
```

**View logs:**

```bash
docker compose logs worker -f
```

### Testing

```bash
# Run all tests
uv run pytest -v

# Run end-to-end integration test
uv run pytest tests/e2e/ -v
```

The e2e test starts Docker Compose services, sends a test message, and verifies processing.

## Health endpoint

The worker exposes a single HTTP endpoint for CDP platform compliance:

| Endpoint       | Description                               |
| :------------- | :---------------------------------------- |
| `GET: /health` | Health check endpoint (returns `{"status": "ok"}`) |

The health endpoint runs on port 8085 via a Flask server in a background thread, using Waitress WSGI server for production-grade concurrent request handling.

**Why Flask instead of FastAPI?** See [docs/cdp-adaptation.md](docs/cdp-adaptation.md) for the architectural decision.

## Pipelines

### Dependabot

We have added an example dependabot configuration file to the repository. You can enable it by renaming
the [.github/example.dependabot.yml](.github/example.dependabot.yml) to `.github/dependabot.yml`

### SonarCloud

Instructions for setting up SonarCloud can be found in [sonar-project.properties](./sonar-project.properties)

## Licence

THIS INFORMATION IS LICENSED UNDER THE CONDITIONS OF THE OPEN GOVERNMENT LICENCE found at:

<http://www.nationalarchives.gov.uk/doc/open-government-licence/version/3>

The following attribution statement MUST be cited in your products and applications when using this information.

> Contains public sector information licensed under the Open Government license v3

### About the licence

The Open Government Licence (OGL) was developed by the Controller of Her Majesty's Stationery Office (HMSO) to enable
information providers in the public sector to license the use and re-use of their information under a common open
licence.

It is designed to encourage use and re-use of information freely and flexibly, with only a few conditions.
