"""Runtime settings: environment variables, Docker secrets and container labels."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SECRETS_DIR = Path("/run/secrets")

LABEL_ENABLE = "sentinel.enable"
LABEL_RESTART_ON_ZERO = "sentinel.restart_on_zero"
LABEL_MAX_RESTARTS = "sentinel.max_restarts"
LABEL_WINDOW_SEC = "sentinel.window_sec"


def _is_true(value: object) -> bool:
    return str(value).strip().lower() == "true"


def _parse_int(value: str | None, default: int) -> int:
    """Parse an integer, falling back to ``default`` when missing or invalid."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Global Sentinel settings read from the environment."""

    max_restarts: int = 3
    time_window_sec: int = 300
    ignore_zero_exit: bool = True
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        return cls(
            max_restarts=int(source.get("SENTINEL_MAX_RESTARTS", "3")),
            time_window_sec=int(source.get("SENTINEL_TIME_WINDOW_SEC", "300")),
            ignore_zero_exit=_is_true(source.get("SENTINEL_IGNORE_ZERO_EXIT", "true")),
            log_level=source.get("LOG_LEVEL", "INFO").upper(),
        )


@dataclass(frozen=True)
class ContainerPolicy:
    """Per-container behaviour resolved from Docker labels and global settings."""

    enabled: bool
    restart_on_zero: bool
    max_restarts: int
    window_sec: int

    @classmethod
    def from_labels(cls, labels: Mapping[str, str] | None, settings: Settings) -> ContainerPolicy:
        """Resolve the policy; an invalid numeric label falls back to the global value."""
        labels = labels or {}
        return cls(
            enabled=_is_true(labels.get(LABEL_ENABLE, "false")),
            restart_on_zero=_is_true(labels.get(LABEL_RESTART_ON_ZERO, "false")),
            max_restarts=_parse_int(labels.get(LABEL_MAX_RESTARTS), settings.max_restarts),
            window_sec=_parse_int(labels.get(LABEL_WINDOW_SEC), settings.time_window_sec),
        )


def get_secret(
    name: str,
    default: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    secrets_dir: Path = DEFAULT_SECRETS_DIR,
) -> str | None:
    """Read a Docker secret (``/run/secrets/<name>``) or fall back to the environment."""
    secret_path = secrets_dir / name.lower()
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()
    source = os.environ if env is None else env
    return source.get(name, default)


@dataclass(frozen=True)
class SmtpSettings:
    """SMTP connection and recipient settings for alert emails."""

    server: str = "smtp.gmail.com"
    port: int = 587
    timeout: int = 15
    user: str | None = None
    password: str | None = None
    recipient: str | None = None

    @property
    def is_complete(self) -> bool:
        return bool(self.user and self.password and self.recipient)

    @classmethod
    def load(
        cls,
        env: Mapping[str, str] | None = None,
        secrets_dir: Path = DEFAULT_SECRETS_DIR,
    ) -> SmtpSettings:
        def read(name: str, default: str | None = None) -> str | None:
            return get_secret(name, default, env=env, secrets_dir=secrets_dir)

        return cls(
            server=read("SMTP_SERVER", "smtp.gmail.com") or "smtp.gmail.com",
            port=int(read("SMTP_PORT", "587") or "587"),
            timeout=int(read("SMTP_TIMEOUT", "15") or "15"),
            user=read("SMTP_USER"),
            password=read("SMTP_PASSWORD"),
            recipient=read("ALERT_EMAIL_TO"),
        )
