from pathlib import Path

import pytest

from tavern_wrapper.config import ConfigError, expand_env, load_config

from .conftest import EXAMPLES

MINIMAL = """
services:
  - name: api
    base_url: https://api.example.com/
    tests: api.tavern.yaml
"""


def test_example_config_is_valid() -> None:
    config = load_config(EXAMPLES / "services.yaml", environ={})
    assert [s.name for s in config.services] == ["httpbin", "httpbin-auth", "httpbin-flow"]
    assert config.alerts.webhook_url is None  # ALERT_WEBHOOK_URL unset -> alerts off
    assert config.services[1].variables == {"user": "demo"}
    assert all(s.tests.is_absolute() and s.tests.is_file() for s in config.services)


def test_defaults_and_relative_paths(write) -> None:
    tests = write("api.tavern.yaml", "---\n")
    config = load_config(write("services.yaml", MINIMAL), environ={})
    assert (config.interval, config.timeout, config.concurrency) == (60, 60, 4)
    assert config.alerts.repeat_after == 3600
    service = config.services[0]
    assert service.base_url == "https://api.example.com"  # trailing slash removed
    assert service.tests == tests.resolve()


def test_env_expansion() -> None:
    env = {"HOST": "api.local", "EMPTY": ""}
    tree = {"a": "http://${HOST}:${PORT:-8080}", "b": ["${EMPTY}", 3], "c": "literal"}
    assert expand_env(tree, env) == {"a": "http://api.local:8080", "b": [None, 3], "c": "literal"}


def test_missing_env_var_is_an_error() -> None:
    with pytest.raises(ConfigError, match="SECRET is not set"):
        expand_env("${SECRET}", {})


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("services: [", "invalid YAML"),
        ("- just\n- a list\n", "must contain a mapping"),
        ("services: []", "invalid configuration"),
        ("interval: 1\n" + MINIMAL, "invalid configuration"),
        (MINIMAL.replace("https://", "ftp://"), "must start with http"),
        (MINIMAL.replace("name: api", "name: 'bad name'"), "invalid configuration"),
        (MINIMAL + MINIMAL.replace("services:\n", ""), "duplicated service names: api"),
        (MINIMAL.replace("api.tavern.yaml", "missing.tavern.yaml"), "not found"),
    ],
)
def test_invalid_configs(write, content: str, message: str) -> None:
    write("api.tavern.yaml", "---\n")
    with pytest.raises(ConfigError, match=message):
        load_config(write("services.yaml", content), environ={})


def test_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "nope.yaml", environ={})
