"""Worker configuration using Pydantic settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerConfig(BaseSettings):
    """Configuration for SQS worker and health server."""

    model_config = SettingsConfigDict(env_prefix="AWS_", case_sensitive=False)
    region: str = "eu-west-2"
    endpoint_url: str | None = None
    sqs_queue_name: str = "nrf_impact_assessment_queue"
    health_port: int = 8085
