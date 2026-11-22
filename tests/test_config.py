"""Settings (config.py): validation of environment values. No network or database."""

from datetime import timedelta

import pytest

from planetary_defense.config import DbConfig, Settings
from planetary_defense.domain.anomaly_rules import AlertThresholds
from planetary_defense.domain.errors import ConfigError

DB = {"DB_NAME": "neo", "DB_USER": "u", "DB_PASSWORD": "secret"}


def test_db_defaults_and_password_hidden_from_repr():
    cfg = Settings(DB).db()
    assert (cfg.host, cfg.port, cfg.dbname) == ("localhost", 5432, "neo")
    assert "secret" not in repr(cfg)


@pytest.mark.parametrize("missing", ["DB_NAME", "DB_USER", "DB_PASSWORD"])
def test_missing_db_setting_is_a_config_error(missing):
    env = {k: v for k, v in DB.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        Settings(env).db()


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


def test_config_error_is_not_a_value_error():
    """A stray `except ValueError` (programmer-bug territory) must not swallow configuration problems."""
    assert not issubclass(ConfigError, ValueError)


def test_alert_thresholds_default_and_override_from_the_environment():
    assert Settings({}).alert_thresholds() == AlertThresholds()
    custom = Settings({"ALERT_WATCH_LD": "12.5", "ALERT_HIGH_VELOCITY_KM_S": "25"}).alert_thresholds()
    assert (custom.watch_ld, custom.high_velocity_km_s, custom.critical_ld) == (12.5, 25.0, 1.0)


@pytest.mark.parametrize(
    ("env", "match"),
    [({"ALERT_WATCH_LD": "far"}, "ALERT_WATCH_LD"), ({"ALERT_CRITICAL_LD": "50"}, "critical_ld")],
)
def test_bad_alert_thresholds_are_config_errors(env, match):
    with pytest.raises(ConfigError, match=match):
        Settings(env).alert_thresholds()


def test_orbit_cache_max_age():
    assert Settings({}).orbit_cache_max_age() == timedelta(days=30)
    assert Settings({"ORBIT_CACHE_MAX_AGE_DAYS": "7"}).orbit_cache_max_age() == timedelta(days=7)
    with pytest.raises(ConfigError, match="ORBIT_CACHE_MAX_AGE_DAYS"):
        Settings({"ORBIT_CACHE_MAX_AGE_DAYS": "-1"}).orbit_cache_max_age()
