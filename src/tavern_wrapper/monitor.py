"""Periodic execution of the checks, latest results and Prometheus metrics."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from prometheus_client import CollectorRegistry, Counter, Gauge

from .alerts import Alerter
from .config import Config, Service
from .runner import ServiceResult, run_service

log = logging.getLogger(__name__)

Runner = Callable[[Service, int], ServiceResult]


class Metrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        labels = ["service"]

        def gauge(name: str, doc: str) -> Gauge:
            return Gauge(name, doc, labels, registry=registry)

        self.up = gauge("tavern_service_up", "1 if every test passed")
        self.tests = gauge("tavern_service_tests", "Tests run in the last check")
        self.failed = gauge("tavern_service_tests_failed", "Tests failed in the last check")
        self.duration = gauge("tavern_service_check_duration_seconds", "Duration of the last check")
        self.last_check = gauge(
            "tavern_service_last_check_timestamp_seconds", "Unix time of the last check"
        )
        self.checks = Counter(
            "tavern_service_checks", "Checks run", labels + ["result"], registry=registry
        )

    def record(self, result: ServiceResult) -> None:
        name = result.service
        self.up.labels(name).set(1 if result.ok else 0)
        self.tests.labels(name).set(result.total)
        self.failed.labels(name).set(result.failed)
        self.duration.labels(name).set(result.duration)
        self.last_check.labels(name).set(result.checked_at.timestamp())
        self.checks.labels(name, "success" if result.ok else "failure").inc()


class Monitor:
    def __init__(
        self,
        config: Config,
        alerter: Alerter | None = None,
        runner: Runner = run_service,
        registry: CollectorRegistry | None = None,
    ) -> None:
        self.config = config
        self.alerter = alerter or Alerter(config.alerts)
        self.registry = registry or CollectorRegistry()
        self.metrics = Metrics(self.registry)
        self._runner = runner
        self._results: dict[str, ServiceResult] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def results(self) -> list[ServiceResult]:
        """Latest result of every service, in the order of the services file."""
        with self._lock:
            return [self._results[s.name] for s in self.config.services if s.name in self._results]

    def check_all(self) -> list[ServiceResult]:
        """Run every service once, in parallel up to `concurrency`."""
        workers = min(self.config.concurrency, len(self.config.services))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(self._check, self.config.services))
        return results

    def _check(self, service: Service) -> ServiceResult:
        try:
            result = self._runner(service, self.config.timeout)
        except Exception as exc:  # the loop must survive any runner bug
            log.exception("check of %s crashed", service.name)
            result = ServiceResult(service.name, service.base_url, ok=False, error=str(exc))
        with self._lock:
            self._results[service.name] = result
        self.metrics.record(result)
        self.alerter.process(result)
        level = logging.INFO if result.ok else logging.WARNING
        log.log(level, "%s: %s", service.name, result.summary)
        return result

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tavern-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.check_all()
            self._stop.wait(self.config.interval)
