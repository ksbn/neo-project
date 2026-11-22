"""Settings: the one place that reads ``.env`` and the process environment.

Entry points call :meth:`Settings.load` once and pass the typed values on.
Adapters never read ``os.environ`` themselves.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import timedelta
from typing import Final, Self

from dotenv import load_dotenv

from planetary_defense.domain.anomaly_rules import AlertThresholds
from planetary_defense.domain.errors import ConfigError

SSL_MODES: Final = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


@dataclass(frozen=True)
class DbConfig:
    """PostgreSQL connection settings."""

    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)  # keep the password out of logs/tracebacks
    sslmode: str = "prefer"  # hosted databases (e.g. Neon) need "require"

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> Self:
        """Build a config from ``DB_*`` variables.

        Raises:
            ConfigError: If a required variable is missing or DB_PORT is not an integer.
        """
        required = ("DB_NAME", "DB_USER", "DB_PASSWORD")
        missing = [name for name in required if not env.get(name)]
        if missing:
            raise ConfigError(f"Missing database settings in environment/.env: {', '.join(missing)}")

        port_raw = env.get("DB_PORT") or "5432"
        try:
            port = int(port_raw)
        except ValueError:
            raise ConfigError(f"DB_PORT must be an integer, got {port_raw!r}") from None

        sslmode = (env.get("DB_SSLMODE") or "prefer").strip()
        if sslmode not in SSL_MODES:
            raise ConfigError(f"DB_SSLMODE must be one of {', '.join(SSL_MODES)}, got {sslmode!r}")

        return cls(
            host=env.get("DB_HOST") or "localhost",
            port=port,
            dbname=env["DB_NAME"],
            user=env["DB_USER"],
            password=env["DB_PASSWORD"],
            sslmode=sslmode,
        )


@dataclass(frozen=True)
class Settings:
    """A snapshot of the environment, validated per use.

    Each accessor validates only what it needs, so a ``--dry-run`` works without
    database settings and the dashboard can show a missing NASA key as a
    diagnostic instead of failing outright.
    """

    env: Mapping[str, str] = field(repr=False)

    @classmethod
    def load(cls) -> Self:
        """Read ``.env`` (without overriding real environment variables) and snapshot the environment."""
        load_dotenv()
        return cls(dict(os.environ))

    def db(self) -> DbConfig:
        return DbConfig.from_mapping(self.env)

    def nasa_api_key(self) -> str:
        """Raises ConfigError if ``NASA_API_KEY`` is unset (use ``DEMO_KEY`` explicitly for a trial)."""
        key = (self.env.get("NASA_API_KEY") or "").strip()
        if not key:
            raise ConfigError("NASA_API_KEY is not set; add it to .env (see .env.example; DEMO_KEY works for a trial)")
        return key

    def alert_thresholds(self) -> AlertThresholds:
        """Anomaly-rule thresholds; each field can be overridden with ALERT_<FIELD> (e.g. ALERT_WATCH_LD=12)."""
        overrides: dict[str, float] = {}
        for f in fields(AlertThresholds):
            name = f"ALERT_{f.name.upper()}"
            raw = (self.env.get(name) or "").strip()
            if raw:
                try:
                    overrides[f.name] = float(raw)
                except ValueError:
                    raise ConfigError(f"{name} must be a number, got {raw!r}") from None
        try:
            return AlertThresholds(**overrides)
        except ValueError as exc:
            raise ConfigError(f"Invalid alert thresholds: {exc}") from None

    def orbit_cache_max_age(self) -> timedelta:
        """How long a cached orbit is used before asking NASA again (ORBIT_CACHE_MAX_AGE_DAYS, default 30)."""
        raw = (self.env.get("ORBIT_CACHE_MAX_AGE_DAYS") or "30").strip()
        try:
            days = int(raw)
        except ValueError:
            raise ConfigError(f"ORBIT_CACHE_MAX_AGE_DAYS must be a whole number, got {raw!r}") from None
        if days < 0:
            raise ConfigError(f"ORBIT_CACHE_MAX_AGE_DAYS must not be negative, got {days}")
        return timedelta(days=days)
