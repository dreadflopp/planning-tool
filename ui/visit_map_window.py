"""Visit map window showing unique name+address combinations."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None

from domain.models import Visit
from services.map_geocoding_service import MapGeocodingService
from services.persistence_service import PersistenceService


@dataclass
class VisitMapMarker:
    marker_id: int
    key: str
    name: str
    address: str
    lat: float
    lng: float
    color_hex: str
    is_office: bool = False


class _GeocodeWorker(QThread):
    finished_ok = Signal(object, object)  # list[VisitMapMarker], list[str]

    def __init__(self,
                 geocode_service: MapGeocodingService,
                 visits: list[Visit],
                 office_address: str,
                 color_palette: dict[str, str]):
        super().__init__()
        self._service = geocode_service
        self._visits = visits
        self._office_address = (office_address or "").strip()
        self._palette = dict(color_palette or {})

    def run(self):
        unique: dict[str, tuple[str, str, str]] = {}
        for visit in self._visits:
            name = (visit.name or "").strip()
            address = (visit.full_address or visit.address or "").strip()
            if not name or not address:
                continue
            key = self._service.hash_name_address(name, address)
            if key not in unique:
                color_key = (visit.color or "").strip().lower()
                unique[key] = (name, address, color_key)
            else:
                _, existing_addr, existing_color = unique[key]
                if not existing_color and visit.color:
                    unique[key] = (name, existing_addr, (visit.color or "").strip().lower())

        markers: list[VisitMapMarker] = []
        errors: list[str] = []
        marker_id = 1

        if self._office_address:
            office_geo = self._service.geocode(self._office_address)
            if office_geo is not None:
                markers.append(
                    VisitMapMarker(
                        marker_id=marker_id,
                        key=self._service.hash_name_address("Kontor", self._office_address),
                        name="Kontor",
                        address=self._office_address,
                        lat=office_geo.lat,
                        lng=office_geo.lng,
                        color_hex=self._palette.get("black", "#2B2B2B"),
                        is_office=True,
                    )
                )
                marker_id += 1
            else:
                errors.append(f"Kunde inte geokoda: Kontor – {self._office_address}")

        for key, (name, address, color_key) in unique.items():
            geocoded = self._service.geocode(address)
            if geocoded is None:
                errors.append(f"Kunde inte geokoda: {name} – {address}")
                continue
            color_hex = self._palette.get(color_key, self._palette.get("blue", "#1F99CD"))
            markers.append(
                VisitMapMarker(
                    marker_id=marker_id,
                    key=key,
                    name=name,
                    address=address,
                    lat=geocoded.lat,
                    lng=geocoded.lng,
                    color_hex=color_hex,
                )
            )
            marker_id += 1

        self.finished_ok.emit(markers, errors)


class VisitMapWindow(QMainWindow):
    def __init__(self, persistence: PersistenceService, api_key: str,
                 office_address: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Besökskarta")
        self.resize(1100, 700)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._geocode_service = MapGeocodingService(persistence, api_key)
        self._worker: Optional[_GeocodeWorker] = None
        self._office_address = (office_address or "").strip()
        self._office_key: Optional[str] = None
        self._did_initial_office_focus = False
        self._color_palette: dict[str, str] = {}
        self._map_page_ready = False
        self._pending_render = False
        self._pending_focus: Optional[tuple[str, bool]] = None
        self._markers: list[VisitMapMarker] = []
        self._visit_to_key: dict[int, str] = {}
        self._key_to_marker: dict[str, VisitMapMarker] = {}
        self._selected_visit_id: Optional[int] = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self._status = QLabel("Kartan laddas...")
        layout.addWidget(self._status)

        if QWebEngineView is None:
            self._map_view = None
            self._status.setText("Kartmodulen saknas. Installera pyside6-addons för kartvisning.")
            return

        self._map_view = QWebEngineView()
        self._map_view.loadFinished.connect(self._on_map_load_finished)
        layout.addWidget(self._map_view, 1)

        template_path = os.path.join(os.path.dirname(__file__), "map_template_google.html")
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()
        html = html.replace("API_KEY_PLACEHOLDER", (api_key or "").strip())
        self._map_view.setHtml(html)

    def _on_map_load_finished(self, ok: bool):
        self._map_page_ready = bool(ok)
        if not ok:
            self._status.setText("Kunde inte ladda kartan.")
            return
        if self._pending_render:
            self._pending_render = False
            self._render_markers()
        if self._pending_focus is not None:
            key, zoom = self._pending_focus
            self._pending_focus = None
            marker = self._key_to_marker.get(key)
            if marker:
                self._focus_marker(marker, zoom=zoom)
                return
        if not self._did_initial_office_focus:
            office = next((m for m in self._markers if m.is_office), None)
            if office is not None:
                self._focus_marker(office, zoom=True)
                self._did_initial_office_focus = True
                return
        self._focus_selected_marker()

    def set_api_key(self, api_key: str):
        self._geocode_service.set_api_key(api_key)

    def set_office_address(self, office_address: str):
        self._office_address = (office_address or "").strip()

    def set_color_palette(self, palette: dict[str, str]):
        self._color_palette = {str(k): str(v) for k, v in (palette or {}).items()}

    def set_visits(self, visits: list[Visit]):
        self._visit_to_key.clear()
        for visit in visits:
            if visit.id is None:
                continue
            name = (visit.name or "").strip()
            address = (visit.full_address or visit.address or "").strip()
            if name and address:
                self._visit_to_key[visit.id] = self._geocode_service.hash_name_address(name, address)

        self._status.setText("Geokodar adresser för kartan...")
        if self._worker is not None and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(500)

        self._worker = _GeocodeWorker(
            self._geocode_service,
            visits,
            self._office_address,
            self._color_palette,
        )
        self._worker.finished_ok.connect(self._on_geocoding_finished)
        self._worker.start()

    def set_selected_visit_id(self, visit_id: Optional[int]):
        self._selected_visit_id = visit_id
        self._focus_selected_marker()

    def _on_geocoding_finished(self, markers: list[VisitMapMarker], errors: list[str]):
        self._markers = markers
        self._key_to_marker = {m.key: m for m in markers}
        office = next((m for m in markers if m.is_office), None)
        self._office_key = office.key if office else None
        self._render_markers()
        if markers:
            status = f"Visar {len(markers)} unika namn/adress-kombinationer."
            if errors:
                status += f" ({len(errors)} kunde inte geokodas)"
            self._status.setText(status)
        else:
            self._status.setText("Inga adresser kunde visas på kartan.")
        if not self._did_initial_office_focus and office is not None:
            self._focus_marker(office, zoom=True)
            self._did_initial_office_focus = True
            return
        self._focus_selected_marker()

    def _render_markers(self):
        if self._map_view is None or self._map_view.page() is None:
            return
        if not self._map_page_ready:
            self._pending_render = True
            return

        self._map_view.page().runJavaScript("clearPins();")
        for marker in self._markers:
            color = marker.color_hex
            text_color = self._text_color_for_background(color)
            name = json.dumps(marker.name)
            info = json.dumps(f"<b>{marker.name}</b><br>{marker.address}")
            js = (
                f"addPin({marker.marker_id}, {marker.lat}, {marker.lng}, {name}, "
                f"'{color}', {info}, '{text_color}', 0, 0);"
            )
            self._map_view.page().runJavaScript(js)

    def _focus_selected_marker(self):
        if self._selected_visit_id is None:
            return
        key = self._visit_to_key.get(self._selected_visit_id)
        if not key:
            return
        marker = self._key_to_marker.get(key)
        if not marker:
            return
        self._focus_marker(marker, zoom=False)

    def _focus_marker(self, marker: VisitMapMarker, zoom: bool):
        if self._map_view is None or self._map_view.page() is None:
            return
        if not self._map_page_ready:
            self._pending_focus = (marker.key, zoom)
            return
        fn = "zoomToPin" if zoom else "panToPin"
        self._map_view.page().runJavaScript(
            f"{fn}({marker.marker_id}, {marker.lat}, {marker.lng});"
        )

    def _text_color_for_background(self, color_hex: str) -> str:
        text = (color_hex or "").strip()
        if len(text) != 7 or not text.startswith("#"):
            return "#FFFFFF"
        try:
            r = int(text[1:3], 16) / 255.0
            g = int(text[3:5], 16) / 255.0
            b = int(text[5:7], 16) / 255.0
        except ValueError:
            return "#FFFFFF"
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        return "#000000" if lum > 0.6 else "#FFFFFF"
