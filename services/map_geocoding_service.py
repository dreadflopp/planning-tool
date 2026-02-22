"""Address geocoding helper for visit map window."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Optional

import requests

from services.persistence_service import PersistenceService

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


@dataclass
class GeocodedLocation:
    address: str
    lat: float
    lng: float
    formatted_address: str = ""


class MapGeocodingService:
    def __init__(self, persistence: PersistenceService, api_key: str):
        self._db = persistence
        self._api_key = (api_key or "").strip()

    def set_api_key(self, key: str):
        self._api_key = (key or "").strip()

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()

    @staticmethod
    def hash_name_address(name: str, address: str) -> str:
        normalized = f"{(name or '').strip().lower()}|{(address or '').strip().lower()}"
        return MapGeocodingService.hash_text(normalized)

    def geocode(self, address: str) -> Optional[GeocodedLocation]:
        text = (address or "").strip()
        if not text:
            return None

        cached = self._db.get_cached_geocode(text)
        if cached is not None:
            lat, lng, formatted = cached
            return GeocodedLocation(
                address=text,
                lat=lat,
                lng=lng,
                formatted_address=formatted,
            )

        if not self._api_key:
            return None

        try:
            resp = requests.get(
                _GEOCODE_URL,
                params={"address": f"{text}, Sweden", "key": self._api_key},
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return None

        if payload.get("status") != "OK":
            return None

        results = payload.get("results") or []
        if not results:
            return None

        result0 = results[0]
        geometry = result0.get("geometry", {})
        loc = geometry.get("location", {})
        if "lat" not in loc or "lng" not in loc:
            return None

        geocoded = GeocodedLocation(
            address=text,
            lat=float(loc["lat"]),
            lng=float(loc["lng"]),
            formatted_address=str(result0.get("formatted_address") or ""),
        )
        self._db.set_cached_geocode(text, geocoded.lat, geocoded.lng, geocoded.formatted_address)
        return geocoded

    def precache_addresses(self, addresses: list[str]) -> tuple[int, int]:
        unique: list[str] = []
        seen: set[str] = set()
        for raw in addresses:
            value = (raw or "").strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(value)

        cached_or_ok = 0
        failed = 0
        for address in unique:
            result = self.geocode(address)
            if result is None:
                failed += 1
            else:
                cached_or_ok += 1
        return cached_or_ok, failed
