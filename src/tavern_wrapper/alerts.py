"""Webhook alerts: one message when a service goes down and one when it recovers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from .config import AlertsConfig
from .runner import ServiceResult

log = logging.getLogger(__name__)


@dataclass
class _AlertState:
    failing: bool = False
    last_sent: float = 0.0


class Alerter:
    """Decides when to notify. The first failure alerts, and so does the recovery;
    while a service stays down the alert is repeated every `repeat_after` seconds."""

    def __init__(
        self,
        config: AlertsConfig,
        send: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._send = send or self._post
        self._clock = clock
        self._states: dict[str, _AlertState] = {}

    @property
    def enabled(self) -> bool:
        return self._config.webhook_url is not None

    def process(self, result: ServiceResult) -> str | None:
        """Update the service state and send a message if needed. Returns that message."""
        state = self._states.setdefault(result.service, _AlertState())
        now = self._clock()
        message: str | None = None

        if not result.ok:
            repeat = self._config.repeat_after
            if not state.failing:
                message = self._down_message(result)
            elif repeat and now - state.last_sent >= repeat:
                message = self._down_message(result, still=True)
            state.failing = True
        elif state.failing:
            message = f":white_check_mark: *{result.service}* recovered ({result.summary})"
            state.failing = False

        if message is None:
            return None
        state.last_sent = now
        if self.enabled:
            try:
                self._send(message)
            except requests.RequestException as exc:
                # A broken webhook must never stop the checks
                log.warning("could not send alert for %s: %s", result.service, exc)
        return message

    @staticmethod
    def _down_message(result: ServiceResult, still: bool = False) -> str:
        verb = "is still failing" if still else "is failing"
        lines = [f":rotating_light: *{result.service}* {verb} ({result.summary})"]
        lines += [f"• {f.name}: {f.message}" for f in result.failures[:10]]
        if len(result.failures) > 10:
            lines.append(f"• …and {len(result.failures) - 10} more")
        return "\n".join(lines)

    def _post(self, message: str) -> None:
        # {"text": ...} is understood by Slack, Mattermost, Rocket.Chat and most chat webhooks
        response = requests.post(
            str(self._config.webhook_url), json={"text": message}, timeout=self._config.timeout
        )
        response.raise_for_status()
