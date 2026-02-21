"""Travel time service – Google Routes API (compute-route-matrix) with local cache."""

from __future__ import annotations

import json
from typing import Optional, Callable

import requests
from PySide6.QtCore import QThread, Signal, QObject

from domain.models import TravelMode
from services.persistence_service import PersistenceService
from domain.constants import API_USAGE_WARN_THRESHOLD, API_USAGE_HARD_LIMIT

_ROUTE_MATRIX_URL = (
    "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
)

_MODE_MAP = {
    TravelMode.CAR: "DRIVE",
    TravelMode.BIKE: "BICYCLE",
    TravelMode.WALK: "WALK",
}


def _build_request_body(from_address: str, to_address: str, mode: str) -> dict:
    travel_mode = _MODE_MAP.get(mode, "DRIVE")
    return {
        "origins": [{"waypoint": {"address": from_address + ", Sweden"}}],
        "destinations": [{"waypoint": {"address": to_address + ", Sweden"}}],
        "travelMode": travel_mode,
        "routingPreference": "TRAFFIC_UNAWARE",
    }


class TravelTimeWorker(QThread):
    """Worker thread that calls the Routes API for one pair."""

    result_ready = Signal(str, str, str, int)    # from, to, mode, minutes
    request_logged = Signal(str)                  # human-readable log entry
    response_logged = Signal(str)
    error_occurred = Signal(str, str, str, str)   # from, to, mode, error_msg
    quota_warning = Signal(int)                   # current usage count

    def __init__(self, from_address: str, to_address: str, mode: str,
                 api_key: str, current_usage: int, parent=None):
        super().__init__(parent)
        self.from_address = from_address
        self.to_address = to_address
        self.mode = mode
        self.api_key = api_key
        self.current_usage = current_usage

    def run(self):
        if self.current_usage >= API_USAGE_HARD_LIMIT:
            self.error_occurred.emit(
                self.from_address, self.to_address, self.mode,
                f"API limit reached ({API_USAGE_HARD_LIMIT} calls). "
                "Increase limit in Settings to continue."
            )
            return

        body = _build_request_body(self.from_address, self.to_address, self.mode)
        self.request_logged.emit(
            f"POST {_ROUTE_MATRIX_URL}\n"
            f"  {self.from_address} → {self.to_address} [{self.mode}]"
        )
        try:
            resp = requests.post(
                _ROUTE_MATRIX_URL,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": "originIndex,destinationIndex,duration,status",
                },
                json=body,
                timeout=15,
            )
        except requests.RequestException as exc:
            msg = f"Network error: {exc}"
            self.response_logged.emit(f"  ERROR: {msg}")
            self.error_occurred.emit(
                self.from_address, self.to_address, self.mode, msg
            )
            return

        if resp.status_code != 200:
            msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
            self.response_logged.emit(f"  ERROR: {msg}")
            self.error_occurred.emit(
                self.from_address, self.to_address, self.mode, msg
            )
            return

        try:
            data = resp.json()
        except ValueError:
            msg = "Invalid JSON response"
            self.response_logged.emit(f"  ERROR: {msg}")
            self.error_occurred.emit(
                self.from_address, self.to_address, self.mode, msg
            )
            return

        # Response is a list; first element is our origin→destination
        if not isinstance(data, list) or not data:
            msg = f"Unexpected response format: {str(data)[:200]}"
            self.response_logged.emit(f"  ERROR: {msg}")
            self.error_occurred.emit(
                self.from_address, self.to_address, self.mode, msg
            )
            return

        element = data[0]
        status = element.get("status", {})
        if status.get("code", 0) != 0:
            msg = f"Route error: {status.get('message', 'unknown')}"
            self.response_logged.emit(f"  ERROR: {msg}")
            self.error_occurred.emit(
                self.from_address, self.to_address, self.mode, msg
            )
            return

        duration_str = element.get("duration", "0s")
        # duration is like "1234s"
        seconds = int(duration_str.rstrip("s")) if duration_str.rstrip("s").isdigit() else 0
        minutes = max(1, round(seconds / 60))

        self.response_logged.emit(
            f"  OK: {minutes} min  (raw: {duration_str})"
        )

        new_usage = self.current_usage + 1
        if new_usage >= API_USAGE_WARN_THRESHOLD:
            self.quota_warning.emit(new_usage)

        self.result_ready.emit(self.from_address, self.to_address, self.mode, minutes)


class TravelTimeService(QObject):
    """Manages travel time lookups: cache-first, API on miss."""

    # Emitted when a travel time is available (from cache or API)
    travel_time_ready = Signal(str, str, str, int)   # from, to, mode, minutes
    request_logged = Signal(str)
    response_logged = Signal(str)
    error_occurred = Signal(str, str, str, str)
    quota_warning = Signal(int)

    def __init__(self, persistence: PersistenceService, api_key: str,
                 settings, parent=None):
        super().__init__(parent)
        self._db = persistence
        self._api_key = api_key
        self._settings = settings
        self._active_workers: list[TravelTimeWorker] = []

    def set_api_key(self, key: str):
        self._api_key = key

    def get_travel_minutes(self, from_address: str, to_address: str,
                           mode: str) -> Optional[int]:
        """Synchronous cache lookup only (no API call)."""
        if from_address == to_address:
            return 0
        return self._db.get_cached_travel(from_address, to_address, mode)

    def get_default_minutes(self, mode: str) -> int:
        return self._settings.default_travel_for_mode(mode)

    def request_travel_time(self, from_address: str, to_address: str, mode: str):
        """Start an async API lookup. Result emitted via travel_time_ready signal."""
        if from_address == to_address:
            self.travel_time_ready.emit(from_address, to_address, mode, 0)
            return

        cached = self._db.get_cached_travel(from_address, to_address, mode)
        if cached is not None:
            self.travel_time_ready.emit(from_address, to_address, mode, cached)
            return

        if not self._api_key:
            default = self.get_default_minutes(mode)
            self.travel_time_ready.emit(from_address, to_address, mode, default)
            return

        worker = TravelTimeWorker(
            from_address, to_address, mode,
            self._api_key, self._settings.api_usage_count,
        )
        worker.result_ready.connect(self._on_result)
        worker.request_logged.connect(self.request_logged)
        worker.response_logged.connect(self.response_logged)
        worker.error_occurred.connect(self._on_error)
        worker.quota_warning.connect(self.quota_warning)
        worker.finished.connect(lambda: self._active_workers.remove(worker))
        self._active_workers.append(worker)
        worker.start()

    def _on_result(self, from_addr: str, to_addr: str, mode: str, minutes: int):
        self._db.set_cached_travel(from_addr, to_addr, mode, minutes)
        new_count = self._db.increment_api_usage()
        self._settings.api_usage_count = new_count
        self.travel_time_ready.emit(from_addr, to_addr, mode, minutes)

    def _on_error(self, from_addr: str, to_addr: str, mode: str, msg: str):
        self.error_occurred.emit(from_addr, to_addr, mode, msg)
        default = self.get_default_minutes(mode)
        self.travel_time_ready.emit(from_addr, to_addr, mode, default)
