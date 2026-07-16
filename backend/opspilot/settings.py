from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings kept outside source control."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_path: Path = Field(default=Path("artifacts/opspilot.db"), alias="OPS_PILOT_DB_PATH")
    demo_namespace: str = Field(default="opspilot-demo", alias="OPS_PILOT_DEMO_NAMESPACE")
    alert_shared_secret: str | None = Field(default=None, alias="OPS_PILOT_ALERT_SHARED_SECRET")
    prometheus_url: str | None = Field(default=None, alias="OPS_PILOT_PROMETHEUS_URL")
    recovery_max_5xx_rate: float = Field(default=0.01, alias="OPS_PILOT_RECOVERY_MAX_5XX_RATE")
    demo_controls_enabled: bool = Field(default=True, alias="OPS_PILOT_DEMO_CONTROLS_ENABLED")
    llm_provider: Literal["openai", "openrouter"] = Field(default="openai", alias="LLM_PROVIDER")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.6-terra", alias="OPENAI_MODEL")
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(
        default="openai/gpt-5.6-luna", alias="OPENROUTER_MODEL"
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    openai_reasoning_effort: Literal["minimal", "low", "medium", "high"] = Field(
        default="medium", alias="OPENAI_REASONING_EFFORT"
    )
    model_price_input_per_million: float | None = Field(
        default=None, alias="MODEL_PRICE_INPUT_PER_MILLION"
    )
    model_price_output_per_million: float | None = Field(
        default=None, alias="MODEL_PRICE_OUTPUT_PER_MILLION"
    )

    @field_validator(
        "model_price_input_per_million", "model_price_output_per_million", mode="before"
    )
    @classmethod
    def blank_price_is_unconfigured(cls, value: object) -> object:
        """Allow optional price placeholders in a local .env file."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def active_model(self) -> str:
        """Return the configured model ID for the selected provider."""

        return self.openrouter_model if self.llm_provider == "openrouter" else self.openai_model

    @property
    def active_api_key(self) -> str | None:
        """Return the credential selected by the local provider setting."""

        return self.openrouter_api_key if self.llm_provider == "openrouter" else self.openai_api_key
