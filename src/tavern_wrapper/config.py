"""Loading and validation of the services file."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, HttpUrl, field_validator

# ${NAME} or ${NAME:-default}, so secrets stay out of the file
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(Exception):
    """The services file is missing, malformed or invalid."""


class AlertsConfig(BaseModel):
    webhook_url: HttpUrl | None = None
    # Re-send the alert while a service keeps failing; 0 disables it
    repeat_after: int = Field(default=3600, ge=0)
    timeout: float = Field(default=10, gt=0)


class Service(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    base_url: str
    tests: Path
    # Extra Tavern variables, available as {name} in the test files
    variables: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value.rstrip("/")


class Config(BaseModel):
    interval: int = Field(default=60, ge=5)
    # Per-service limit for a whole Tavern run
    timeout: int = Field(default=60, ge=1)
    concurrency: int = Field(default=4, ge=1)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    services: list[Service] = Field(min_length=1)

    @field_validator("services")
    @classmethod
    def _unique_names(cls, services: list[Service]) -> list[Service]:
        names = [s.name for s in services]
        duplicated = sorted({n for n in names if names.count(n) > 1})
        if duplicated:
            raise ValueError(f"duplicated service names: {', '.join(duplicated)}")
        return services


def expand_env(value: Any, environ: dict[str, str] | None = None) -> Any:
    """Replace ${NAME} references in every string of a parsed YAML tree."""
    env = os.environ if environ is None else environ
    if isinstance(value, dict):
        return {k: expand_env(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v, env) for v in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in env:
            return env[name]
        if default is not None:
            return default
        raise ConfigError(f"environment variable {name} is not set")

    expanded = _ENV_PATTERN.sub(replace, value)
    # An optional setting left empty (e.g. no webhook) means "not set"
    return None if expanded == "" and value != "" else expanded


def load_config(path: Path, environ: dict[str, str] | None = None) -> Config:
    """Read the services file; test paths are resolved against its directory."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping")

    try:
        config = Config.model_validate(expand_env(raw, environ))
    except ValueError as exc:
        raise ConfigError(f"invalid configuration in {path}: {exc}") from exc

    base = path.parent
    for service in config.services:
        if not service.tests.is_absolute():
            service.tests = (base / service.tests).resolve()
        if not service.tests.is_file():
            raise ConfigError(f"service {service.name}: test file {service.tests} not found")
    return config
