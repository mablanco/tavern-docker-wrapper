import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tavern_wrapper import __main__ as cli
from tavern_wrapper.alerts import Alerter
from tavern_wrapper.config import AlertsConfig, Config, Service
from tavern_wrapper.monitor import Monitor
from tavern_wrapper.runner import FailedTest, ServiceResult
from tavern_wrapper.web import create_app

from .conftest import EXAMPLES


def _config(tmp_path: Path, *names: str) -> Config:
    tests = tmp_path / "x.tavern.yaml"
    tests.write_text("---\n")
    services = [Service(name=n, base_url=f"http://{n}", tests=tests) for n in names]
    return Config(interval=5, services=services)


def _fake_runner(service: Service, timeout: int) -> ServiceResult:
    if service.name == "crash":
        raise RuntimeError("runner bug")
    if service.name == "down":
        return ServiceResult(
            service.name,
            service.base_url,
            ok=False,
            total=2,
            failed=1,
            failures=[FailedTest("login", "status 500 != 200")],
        )
    return ServiceResult(service.name, service.base_url, ok=True, total=3, duration=0.25)


@pytest.fixture
def monitor(tmp_path: Path) -> Monitor:
    config = _config(tmp_path, "up", "down", "crash")
    sent: list[str] = []
    alerter = Alerter(AlertsConfig(webhook_url="https://hooks.example.com/x"), send=sent.append)
    monitor = Monitor(config, alerter=alerter, runner=_fake_runner)
    monitor.sent = sent
    return monitor


def test_check_all_records_results_metrics_and_alerts(monitor: Monitor) -> None:
    results = monitor.check_all()
    assert [r.ok for r in results] == [True, False, False]
    assert [r.service for r in monitor.results] == ["up", "down", "crash"]
    assert monitor.results[2].error == "runner bug"
    assert len(monitor.sent) == 2  # one alert per failing service

    sample = monitor.registry.get_sample_value
    assert sample("tavern_service_up", {"service": "up"}) == 1
    assert sample("tavern_service_up", {"service": "down"}) == 0
    assert sample("tavern_service_tests", {"service": "up"}) == 3
    assert sample("tavern_service_tests_failed", {"service": "down"}) == 1
    assert sample("tavern_service_check_duration_seconds", {"service": "up"}) == 0.25
    assert sample("tavern_service_checks_total", {"service": "down", "result": "failure"}) == 1
    assert sample("tavern_service_last_check_timestamp_seconds", {"service": "up"}) > 0


def test_background_loop(monitor: Monitor) -> None:
    monitor.start()
    deadline = time.monotonic() + 5
    while len(monitor.results) < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    monitor.stop()
    assert len(monitor.results) == 3


def test_web_before_first_check(monitor: Monitor) -> None:
    client = TestClient(create_app(monitor, run_in_background=False))
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/status").json() == {"ok": False, "services": []}
    page = client.get("/")
    assert page.status_code == 200
    assert "Waiting for the first checks" in page.text


def test_web_with_results(monitor: Monitor) -> None:
    monitor.check_all()
    client = TestClient(create_app(monitor, run_in_background=False))

    status = client.get("/api/status").json()
    assert status["ok"] is False
    down = status["services"][1]
    assert down["service"] == "down" and down["passed"] == 1
    assert down["failures"] == [{"name": "login", "message": "status 500 != 200"}]

    page = client.get("/").text
    assert "2 services failing" in page
    assert "status 500 != 200" in page
    assert '<meta http-equiv="refresh" content="5">' in page

    metrics = client.get("/metrics")
    assert metrics.headers["content-type"].startswith("text/plain")
    assert 'tavern_service_up{service="up"} 1.0' in metrics.text


def test_web_all_healthy(tmp_path: Path) -> None:
    monitor = Monitor(_config(tmp_path, "up"), runner=_fake_runner)
    monitor.check_all()
    client = TestClient(create_app(monitor, run_in_background=False))
    assert client.get("/api/status").json()["ok"] is True
    assert "All services healthy" in client.get("/").text


def test_app_lifespan_starts_and_stops_monitor(monitor: Monitor) -> None:
    with TestClient(create_app(monitor)) as client:
        assert client.get("/healthz").status_code == 200
    assert monitor._stop.is_set()


def test_cli_once(httpbin: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEMO_PASSWORD", "s3cret")
    config = (EXAMPLES / "services.yaml").read_text().replace("http://httpbin:8080", httpbin)
    (tmp_path / "services.yaml").write_text(config)
    (tmp_path / "tests").symlink_to(EXAMPLES / "tests")
    assert cli.main(["--once", "-c", str(tmp_path / "services.yaml")]) == 0

    broken = config.replace(httpbin, "http://127.0.0.1:9")
    (tmp_path / "services.yaml").write_text(broken)
    assert cli.main(["--once", "-c", str(tmp_path / "services.yaml")]) == 1


def test_cli_bad_config(tmp_path: Path) -> None:
    assert cli.main(["-c", str(tmp_path / "missing.yaml")]) == 2


def test_cli_serves(monkeypatch) -> None:
    calls = {}
    monkeypatch.setenv("DEMO_PASSWORD", "x")
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda app, host, port, log_level: calls.update(host=host, port=port)
    )
    assert cli.main(["-c", str(EXAMPLES / "services.yaml"), "--port", "9999"]) == 0
    assert calls == {"host": "0.0.0.0", "port": 9999}
