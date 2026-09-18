"""Bounded, persistent cloud-assisted recovery of a failed LAN service."""

from __future__ import annotations

from typing import Any


class RecoveryPolicy:
    """Count actual failed probes; reserve attempts before any reboot request."""

    def __init__(self) -> None:
        self.history: dict[str, dict[str, Any]] = {}
        self.failures: dict[str, tuple[str, float, int]] = {}

    def restore(self, value: Any) -> None:
        """Restore only validated attempt records from HA storage."""
        if not isinstance(value, dict):
            return
        for device, record in value.items():
            if (
                isinstance(device, str)
                and isinstance(record, dict)
                and type(record.get("last_attempt")) in (int, float)
                and 0 <= record["last_attempt"] < 1e12
                and type(record.get("attempted_outage")) is bool
            ):
                self.history[device] = {
                    "last_attempt": record["last_attempt"],
                    "attempted_outage": record["attempted_outage"],
                }

    def failed(self, device: str, host: str, now: float) -> None:
        """Track consecutive failures against the same address."""
        previous = self.failures.get(device)
        if previous is None or previous[0] != host:
            self.failures[device] = (host, now, 1)
        else:
            self.failures[device] = (host, previous[1], previous[2] + 1)

    def recovered(self, device: str) -> None:
        """End an outage without clearing the cross-outage cooldown."""
        self.failures.pop(device, None)
        if device in self.history:
            self.history[device]["attempted_outage"] = False

    def due(self, device: str, now: float, delay: float, cooldown: float) -> bool:
        """Require three actual probes, sustained failure and an unused attempt."""
        failure = self.failures.get(device)
        history = self.history.get(device, {})
        return bool(
            failure
            and failure[2] >= 3
            and now - failure[1] >= delay
            and not history.get("attempted_outage", False)
            and now - history.get("last_attempt", float("-inf")) >= cooldown
        )

    def reserve(self, device: str, now: float) -> None:
        """Consume one attempt even if the network response is later lost."""
        self.history[device] = {"last_attempt": now, "attempted_outage": True}
