"""Strict instance configuration; secrets are read from files at runtime."""

import tomllib
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from cronsim import CronSim
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Access(StrictModel):
    allowed_users: list[int] = Field(default_factory=list)
    allowed_chats: list[int] = Field(default_factory=list)
    outgoing_chats: list[int] = Field(default_factory=list)
    public: bool = False
    groups: bool = False


class Attachments(StrictModel):
    download: bool = True
    download_denied: bool = False
    max_bytes: int = Field(default=20_000_000, gt=0)
    timeout: int = Field(default=60, gt=0, le=300)
    upload_roots: list[str] = Field(default_factory=list)


class Gateway(StrictModel):
    url: str
    target: str = Field(min_length=1)
    start: str = Field(pattern="^(oldest|latest)$")
    token_file: str | None = None

    @field_validator("url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in value
            or "#" in value
        ):
            raise ValueError(
                "Gateway requires an HTTP(S) base URL without userinfo, query or fragment"
            )
        # Accessing port also rejects malformed or out-of-range port values.
        _ = parsed.port
        return value


class Config(StrictModel):
    token_file: str
    api_token_file: str
    data_dir: str
    port: int = Field(default=10881, ge=1024, le=65535)
    timezone: str = "UTC"
    rotate: str = "0 0 * * *"
    access: Access = Field(default_factory=Access)
    attachments: Attachments = Field(default_factory=Attachments)
    gateway: Gateway | None = None
    notify_chats: list[int] = Field(default_factory=list)
    # Accepted for existing configurations; denials are never time-filtered.
    notify_interval: int = Field(default=300, ge=1)

    @model_validator(mode="after")
    def validate_settings(self) -> "Config":
        from datetime import datetime

        zone = ZoneInfo(self.timezone)
        if len(self.rotate.split()) != 5:
            raise ValueError("rotate must be a five-field cron expression")
        next(CronSim(self.rotate, datetime.now(zone)))
        if not set(self.notify_chats) <= set(self.access.outgoing_chats):
            raise ValueError("Notification chats must be allowed outgoing destinations")
        return self


def secret(path: str) -> str:
    value = Path(path).expanduser().read_text().strip()
    if not value:
        raise ValueError("Secret file is empty")
    return value


def load(path: Path) -> Config:
    return Config.model_validate(tomllib.loads(path.read_text()))
