from pathlib import Path

import pytest

from tavern_wrapper.config import Service, load_config
from tavern_wrapper.runner import parse_junit, run_service

from .conftest import EXAMPLES

PASSING = """
---
test_name: health is up
strict: [json:off]
stages:
  - name: get
    request:
      url: "{base_url}/get"
      method: GET
      params: {probe: "{probe}"}
    response:
      status_code: 200
      json:
        args:
          probe: ["{probe}"]
"""

FAILING = """
---
test_name: wrong status
stages:
  - name: expects 200 but gets 500
    request:
      url: "{base_url}/status/500"
      method: GET
    response:
      status_code: 200
---
test_name: right status
stages:
  - name: expects 204
    request:
      url: "{base_url}/status/204"
      method: GET
    response:
      status_code: 204
"""


def _service(path: Path, base_url: str, **variables: object) -> Service:
    return Service(name="svc", base_url=base_url, tests=path, variables=variables)


@pytest.mark.parametrize(
    ("name", "tests"), [("httpbin", 2), ("httpbin-auth", 1), ("httpbin-flow", 1)]
)
def test_examples_pass_against_httpbin(httpbin: str, monkeypatch, name: str, tests: int) -> None:
    monkeypatch.setenv("DEMO_PASSWORD", "s3cret")
    config = load_config(EXAMPLES / "services.yaml", environ={})
    service = next(s for s in config.services if s.name == name)
    service.base_url = httpbin
    result = run_service(service, timeout=60)
    assert result.ok, result.failures or result.error
    assert result.total == tests and result.failed == 0
    assert result.summary == f"{tests}/{tests} tests passed"


def test_variables_reach_tavern(httpbin: str, write) -> None:
    result = run_service(_service(write("ok.tavern.yaml", PASSING), httpbin, probe="x"), 60)
    assert result.ok and result.total == 1


def test_failures_are_reported(httpbin: str, write) -> None:
    result = run_service(_service(write("bad.tavern.yaml", FAILING), httpbin), 60)
    assert not result.ok
    assert (result.total, result.failed, result.passed) == (2, 1, 1)
    assert result.failures[0].name.endswith("wrong status")
    assert result.failures[0].message
    assert result.summary == "1/2 tests passed"


def test_unreachable_service(write) -> None:
    # Port 9 (discard) is closed on any sane test machine
    result = run_service(
        _service(write("ok.tavern.yaml", PASSING), "http://127.0.0.1:9", probe="x"), 60
    )
    assert not result.ok and result.failed == 1


def test_broken_test_file(httpbin: str, write) -> None:
    result = run_service(_service(write("broken.tavern.yaml", "---\nfoo: bar\n"), httpbin), 60)
    assert not result.ok
    assert result.failed == result.total


def test_empty_test_file(httpbin: str, write) -> None:
    result = run_service(_service(write("empty.tavern.yaml", "# nothing\n"), httpbin), 60)
    assert not result.ok
    assert result.error and result.error.startswith("no tests collected")


def test_timeout(httpbin: str, write) -> None:
    result = run_service(_service(write("ok.tavern.yaml", PASSING), httpbin, probe="x"), 0)
    assert not result.ok
    assert result.error == "timed out after 0s"


def test_parse_junit_skips_and_errors(write) -> None:
    report = write(
        "report.xml",
        """<testsuites><testsuite>
          <testcase name="ok"/>
          <testcase name="skipped"><skipped/></testcase>
          <testcase name="failed"><failure message="boom&#10;details"/></testcase>
          <testcase name="errored"><error>
            first line
          </error></testcase>
        </testsuite></testsuites>""",
    )
    total, failures = parse_junit(report)
    assert total == 3
    assert [(f.name, f.message) for f in failures] == [
        ("failed", "boom"),
        ("errored", "first line"),
    ]
