import pytest
import requests

from tavern_wrapper.alerts import Alerter
from tavern_wrapper.config import AlertsConfig
from tavern_wrapper.runner import FailedTest, ServiceResult


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _result(ok: bool, failures: int = 0) -> ServiceResult:
    items = [FailedTest(f"test {i}", f"error {i}") for i in range(failures)]
    return ServiceResult(
        "api", "http://api", ok=ok, total=max(failures, 1), failed=failures, failures=items
    )


@pytest.fixture
def setup():
    sent: list[str] = []
    clock = Clock()
    config = AlertsConfig(webhook_url="https://hooks.example.com/x", repeat_after=600)
    return Alerter(config, send=sent.append, clock=clock), sent, clock


def test_down_then_recovered(setup) -> None:
    alerter, sent, _ = setup
    assert alerter.process(_result(True)) is None  # healthy from the start: silence
    alerter.process(_result(False, failures=2))
    alerter.process(_result(False, failures=2))  # still down, before repeat_after
    alerter.process(_result(True))
    assert len(sent) == 2
    assert sent[0].startswith(":rotating_light: *api* is failing (0/2 tests passed)")
    assert "• test 1: error 1" in sent[0]
    assert sent[1].startswith(":white_check_mark: *api* recovered")


def test_repeat_after(setup) -> None:
    alerter, sent, clock = setup
    alerter.process(_result(False, failures=1))
    clock.now += 599
    alerter.process(_result(False, failures=1))
    clock.now += 1
    alerter.process(_result(False, failures=1))
    assert len(sent) == 2
    assert "is still failing" in sent[1]


def test_repeat_disabled() -> None:
    sent: list[str] = []
    clock = Clock()
    config = AlertsConfig(webhook_url="https://hooks.example.com/x", repeat_after=0)
    alerter = Alerter(config, send=sent.append, clock=clock)
    alerter.process(_result(False, failures=1))
    clock.now += 10**6
    alerter.process(_result(False, failures=1))
    assert len(sent) == 1


def test_long_failure_lists_are_truncated(setup) -> None:
    alerter, sent, _ = setup
    alerter.process(_result(False, failures=13))
    assert sent[0].count("\n• test") == 10
    assert sent[0].endswith("• …and 3 more")


def test_without_webhook_nothing_is_sent() -> None:
    sent: list[str] = []
    alerter = Alerter(AlertsConfig(), send=sent.append)
    assert not alerter.enabled
    assert alerter.process(_result(False, failures=1))  # message still computed for the log
    assert sent == []


def test_webhook_errors_do_not_propagate(setup) -> None:
    alerter, _, _ = setup

    def boom(_: str) -> None:
        raise requests.ConnectionError("down")

    alerter._send = boom
    assert alerter.process(_result(False, failures=1))


def test_post_payload(monkeypatch) -> None:
    calls = []

    class Response:
        def raise_for_status(self) -> None:
            pass

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    config = AlertsConfig(webhook_url="https://hooks.example.com/x", timeout=3)
    Alerter(config).process(_result(False, failures=1))
    url, payload, timeout = calls[0]
    assert url == "https://hooks.example.com/x"
    assert payload["text"].startswith(":rotating_light:")
    assert timeout == 3
