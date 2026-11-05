"""Runs the Tavern tests of one service and turns the JUnit report into a result."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET  # noqa: S405 - the report is written by our own pytest run
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .config import Service


@dataclass
class FailedTest:
    name: str
    message: str


@dataclass
class ServiceResult:
    service: str
    base_url: str
    ok: bool
    total: int = 0
    failed: int = 0
    duration: float = 0.0
    failures: list[FailedTest] = field(default_factory=list)
    # Set when Tavern could not run at all (timeout, broken test file...)
    error: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def passed(self) -> int:
        return self.total - self.failed

    @property
    def summary(self) -> str:
        if self.error:
            return self.error
        return f"{self.passed}/{self.total} tests passed"


def parse_junit(path: Path) -> tuple[int, list[FailedTest]]:
    """Return the number of tests and the failed ones from a pytest JUnit report."""
    root = ET.parse(path).getroot()  # noqa: S314
    total = 0
    failures: list[FailedTest] = []
    for case in root.iter("testcase"):
        if case.find("skipped") is not None:
            continue
        total += 1
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is not None:
            message = (problem.get("message") or problem.text or "").strip()
            failures.append(FailedTest(case.get("name", "?"), _first_line(message)))
    return total, failures


def _first_line(text: str, limit: int = 300) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line if len(line) <= limit else line[: limit - 1] + "…"


def run_service(service: Service, timeout: int) -> ServiceResult:
    """Run every Tavern test of a service in its own pytest process."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"tavern-{service.name}-") as tmp:
        workdir = Path(tmp)
        global_cfg = workdir / "global.yaml"
        global_cfg.write_text(
            yaml.safe_dump({"variables": {**service.variables, "base_url": service.base_url}}),
            encoding="utf-8",
        )
        report = workdir / "report.xml"
        # Empty ini file: pytest must not pick up any configuration around the tests
        ini = workdir / "pytest.ini"
        ini.write_text("[pytest]\n", encoding="utf-8")
        command = [
            sys.executable, "-m", "pytest", str(service.tests),
            "-c", str(ini),
            "--rootdir", str(service.tests.parent),
            "-p", "no:cacheprovider",
            f"--tavern-global-cfg={global_cfg}",
            f"--junitxml={report}",
            "-q", "--no-header",
        ]  # fmt: skip
        try:
            proc = subprocess.run(
                command, cwd=workdir, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return _failed(service, f"timed out after {timeout}s", started)

        if not report.is_file():
            return _failed(service, _pytest_error(proc), started)
        total, failures = parse_junit(report)

    duration = time.monotonic() - started
    if total == 0:
        return _failed(service, _pytest_error(proc, "no tests collected"), started)
    return ServiceResult(
        service=service.name,
        base_url=service.base_url,
        ok=not failures,
        total=total,
        failed=len(failures),
        duration=duration,
        failures=failures,
    )


def _pytest_error(proc: subprocess.CompletedProcess[str], default: str = "pytest failed") -> str:
    output = (proc.stdout + proc.stderr).strip().splitlines()
    detail = next((ln for ln in reversed(output) if ln.strip()), "")
    return f"{default}: {_first_line(detail)}" if detail else default


def _failed(service: Service, error: str, started: float) -> ServiceResult:
    return ServiceResult(
        service=service.name,
        base_url=service.base_url,
        ok=False,
        duration=time.monotonic() - started,
        error=error,
    )
