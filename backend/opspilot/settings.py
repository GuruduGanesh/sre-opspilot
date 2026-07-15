from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings kept outside source control."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_path: Path = Field(default=Path("artifacts/opspilot.db"), alias="OPS_PILOT_DB_PATH")
    demo_namespace: str = Field(default="opspilot-demo", alias="OPS_PILOT_DEMO_NAMESPACE")
    alert_shared_secret: str | None = Field(default=None, alias="OPS_PILOT_ALERT_SHARED_SECRET")
    openai_model: str = Field(default="gpt-5.6-terra", alias="OPENAI_MODEL")
    openai_reasoning_effort: Literal["minimal", "low", "medium", "high"] = Field(
        default="medium", alias="OPENAI_REASONING_EFFORT"
    )
    model_price_input_per_million: float | None = Field(
        default=None, alias="MODEL_PRICE_INPUT_PER_MILLION"
    )
    model_price_output_per_million: float | None = Field(
        default=None, alias="MODEL_PRICE_OUTPUT_PER_MILLION"
    )
