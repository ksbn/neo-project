"""Settings (config.py): validation of environment values. No network or database."""

import pytest

from config import DbConfig, Settings
from errors import ConfigError, ExitCode

DB = {"DB_NAME": "neo", "DB_USER": "u", "DB_PASSWORD": "secret"}


def test_db_defaults_and_password_hidden_from_repr():
    cfg = Settings(DB).db()
    assert (cfg.host, cfg.port, cfg.dbname) == ("localhost", 5432, "neo")
    assert "secret" not in repr(cfg)


@pytest.mark.parametrize("missing", ["DB_NAME", "DB_USER", "DB_PASSWORD"])
def test_missing_db_setting_is_a_config_error(missing):
    env = {k: v for k, v in DB.items() if k != missing}
    with pytest.raises(ConfigError, match=missing) as exc:
        Settings(env).db()
    assert exc.value.exit_code == ExitCode.CONFIG_ERROR


def test_non_integer_port_is_a_config_error():
    with pytest.raises(ConfigError, match="DB_PORT must be an integer"):
        DbConfig.from_mapping({**DB, "DB_PORT": "abc"})


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_nasa_key_is_a_config_error(value):
    env = {} if value is None else {"NASA_API_KEY": value}
    with pytest.raises(ConfigError, match="NASA_API_KEY"):
        Settings(env).nasa_api_key()


def test_settings_validate_lazily():
    """A dry run needs only the NASA key; the dashboard can still report a missing key."""
    settings = Settings({"NASA_API_KEY": "k"})
    assert settings.nasa_api_key() == "k"
    with pytest.raises(ConfigError):
        settings.db()


def test_config_error_is_still_a_value_error():
    assert issubclass(ConfigError, ValueError)
