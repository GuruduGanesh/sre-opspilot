import json
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class AlertStatus(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


class AlertmanagerAlert(BaseModel):
    """One alert from Alertmanager's generic webhook v4 payload."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: AlertStatus
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generator_url: HttpUrl | None = Field(default=None, alias="generatorURL")
    fingerprint: str

    @field_validator("fingerprint")
    @classmethod
    def fingerprint_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fingerprint must not be blank")
        return value


class AlertmanagerWebhookV4(BaseModel):
    """Strictly typed Alertmanager generic webhook v4 contract."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: Literal["4"]
    group_key: str = Field(alias="groupKey")
    truncated_alerts: int = Field(ge=0, alias="truncatedAlerts")
    status: AlertStatus
    receiver: str
    group_labels: dict[str, str] = Field(alias="groupLabels")
    common_labels: dict[str, str] = Field(alias="commonLabels")
    common_annotations: dict[str, str] = Field(alias="commonAnnotations")
    external_url: HttpUrl | None = Field(default=None, alias="externalURL")
    notification_reason: str | None = Field(default=None, alias="notification_reason")
    alerts: list[AlertmanagerAlert] = Field(min_length=1)

    @field_validator("group_key", "receiver")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value

    @model_validator(mode="after")
    def all_alerts_match_group_status(self) -> "AlertmanagerWebhookV4":
        if any(alert.status != self.status for alert in self.alerts):
            raise ValueError("all alerts must match the group status for this demo ingress")
        return self

    def incident_key(self) -> str:
        """Stable across firing and resolved deliveries for the same alert group."""

        key_material = {
            "version": self.version,
            "group_key": self.group_key,
            "fingerprints": sorted(alert.fingerprint for alert in self.alerts),
        }
        return sha256(_canonical_json(key_material).encode()).hexdigest()

    def delivery_hash(self) -> str:
        """Idempotency key for an exact webhook delivery or retry."""

        return sha256(
            _canonical_json(self.model_dump(by_alias=True, mode="json")).encode()
        ).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
