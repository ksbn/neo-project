"""PostgreSQL connections. The schema itself is managed by dbmate (``db/migrations``)."""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from planetary_defense.config import DbConfig
from planetary_defense.domain.errors import StorageError

__all__ = ["connect"]

logger = logging.getLogger(__name__)


def connect(
    config: DbConfig,
    *,
    connect_timeout: int = 10,
    application_name: str = "neo-pipeline",
    **kwargs: Any,
) -> psycopg.Connection:
    """Open a new connection (autocommit off). Use it as a context manager.

    Raises:
        StorageError: If the server cannot be reached or rejects the login.
    """
    logger.debug("Connecting to %s", config)
    try:
        return psycopg.connect(
            host=config.host,
            port=config.port,
            dbname=config.dbname,
            user=config.user,
            password=config.password,
            sslmode=config.sslmode,
            connect_timeout=connect_timeout,
            application_name=application_name,
            **kwargs,
        )
    except psycopg.Error as exc:
        raise StorageError(f"Could not connect to PostgreSQL at {config.host}:{config.port}: {exc}") from exc
