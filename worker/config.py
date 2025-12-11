"""Worker configuration using Pydantic settings."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerConfig(BaseSettings):
    """Configuration for SQS worker and health server.

    Environment variables (optional, for LocalStack/testing only):
    - SQS_ENDPOINT: SQS endpoint URL (for LocalStack only)
    - SQS_WAIT_TIME_SECONDS: Long polling wait time (default: 20)
    - HEALTH_PORT: Health check port (default: 8085)
    - HEARTBEAT_TIMEOUT: Max seconds between heartbeats before unhealthy (default: 120)
    - TASK_TIMEOUT_BUFFER: Multiplier for long task timeout (default: 1.5)
    """

    model_config = SettingsConfigDict(case_sensitive=False)

    # CDP configuration (fixed values for CDP platform)
    region: str = "eu-west-2"  # All CDP environments use eu-west-2
    sqs_endpoint: str | None = None  # Only needed for LocalStack

    # Application configuration
    sqs_queue_name: str = "nrf_impact_assessment_queue"  # Service name for consistency
    sqs_wait_time_seconds: int = 20  # SQS long polling wait time (max: 20)
    health_port: int = 8085

    # Health check configuration (reduced from 180s now that we have retry logic)
    heartbeat_timeout: int = 120  # seconds - max time between heartbeats
    task_timeout_buffer: float = 1.5  # multiplier for long task timeout

    @property
    def endpoint_url(self) -> str | None:
        """SQS endpoint URL for boto3 client."""
        return self.sqs_endpoint

    @field_validator("sqs_wait_time_seconds")
    @classmethod
    def validate_wait_time(cls, v: int) -> int:
        """Validate SQS wait time is within AWS limits (0-20 seconds)."""
        if not 0 <= v <= 20:
            msg = f"Wait time must be between 0 and 20 seconds, got {v}"
            raise ValueError(msg)
        return v
