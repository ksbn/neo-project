"""Settings: the one place that reads ``.env`` and the process environment.

Entry points call :meth:`Settings.load` once and pass the typed values on.
Adapters never read ``os.environ`` themselves.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Self

from dotenv import load_dotenv

from errors import ConfigError


@dataclass(frozen=True)
class DbConfig:
    """PostgreSQL connection settings."""

    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)  # keep the password out of logs/tracebacks

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

        return cls(
            host=env.get("DB_HOST") or "localhost",
            port=port,
            dbname=env["DB_NAME"],
            user=env["DB_USER"],
            password=env["DB_PASSWORD"],
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
