"""Domain models for the route planning tool."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations (stored as plain strings in DB for simplicity)
# ---------------------------------------------------------------------------

class TravelMode:
    CAR = "car"
    BIKE = "bike"
    WALK = "walk"
    ALL = ("car", "bike", "walk")


class TravelTimeState:
    DEFAULT = "default"
    CALCULATED = "calculated"
    EDITED = "edited"


class VisitColor:
    GREEN = "green"
    PINK = "pink"
    BLUE = "blue"
    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    BLACK = "black"

    SWEDISH_MAP = {
        "grön": "green",
        "rosa": "pink",
        "blå": "blue",
        "röd": "red",
        "orange": "orange",
        "gul": "yellow",
        "svart": "black",
    }


def _normalize_hex_color(value: str, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    if not text.startswith("#"):
        text = f"#{text}"
    if len(text) == 7 and all(ch in "0123456789ABCDEFabcdef" for ch in text[1:]):
        return text.upper()
    return fallback


# ---------------------------------------------------------------------------
# Core domain objects
# ---------------------------------------------------------------------------

@dataclass
class Visit:
    """A single care visit imported from Excel."""
    id: Optional[int]
    object_id: str               # unique key from Excel (ObjectId column)
    name: str                    # patient name
    address: str                 # cleaned address (postal code + city stripped) – for display
    street: str                  # extracted street name (before first digit)
    default_start: str           # HH:MM – original scheduled start
    default_end: str             # HH:MM – original scheduled end
    insatser: str                # comma-separated service tags
    color: Optional[str] = None  # 'green' | 'pink' | 'blue' | None
    raw_data: str = ""           # JSON blob of original Excel row
    full_address: str = ""       # original address including postal code – used for travel API
    # Runtime-only (not persisted in visits table)
    pair_partner_id: Optional[int] = field(default=None, compare=False, repr=False)


@dataclass
class RouteEntry:
    """One visit (or office instance) placed in a route at a specific time."""
    id: Optional[int]
    route_id: int
    visit_id: Optional[int]      # None for office instances
    position: int                # order within route (0-based)
    start_time: str              # HH:MM – actual scheduled start (may differ from visit default)
    end_time: str                # HH:MM – actual scheduled end
    is_office_instance: bool = False
    office_name: str = ""
    office_address: str = ""
    office_color: Optional[str] = "black"
    route_color: Optional[str] = field(default=None, compare=False, repr=False)
    # Runtime-only
    visit: Optional[Visit] = field(default=None, compare=False, repr=False)

    @property
    def display_name(self) -> str:
        if self.is_office_instance:
            return self.office_name or "Kontor"
        return self.visit.name if self.visit else "?"

    @property
    def display_address(self) -> str:
        if self.is_office_instance:
            return (self.office_address or "").split(",", 1)[0].strip()
        if not self.visit:
            return ""
        return (self.visit.address or "").split(",", 1)[0].strip()

    @property
    def display_insatser(self) -> str:
        if self.is_office_instance:
            return ""
        return self.visit.insatser if self.visit else ""

    @property
    def display_color(self) -> Optional[str]:
        if self.is_office_instance:
            return self.office_color or "black"
        if self.visit and self.visit.color:
            return self.visit.color
        return self.route_color

    @property
    def api_address(self) -> str:
        """Full address (with postal code) for travel time API lookups."""
        if self.is_office_instance:
            return self.office_address
        if self.visit:
            return self.visit.full_address or self.visit.address
        return ""


@dataclass
class TravelSegment:
    """Travel time block between two consecutive route entries."""
    id: Optional[int]
    route_id: int
    from_entry_id: int
    to_entry_id: int
    mode: str                          # TravelMode constant
    travel_minutes: int
    is_custom: bool = False            # True when user manually edited
    calculated_minutes: Optional[int] = None  # value from API (for restore)
    # Computed display times (set by recalculation engine, not persisted)
    start_time: str = ""
    end_time: str = ""
    # Runtime-only API status (not persisted)
    is_calculating: bool = field(default=False, compare=False, repr=False)
    loading_display_is_calc: Optional[bool] = field(default=None, compare=False, repr=False)
    api_failed: bool = field(default=False, compare=False, repr=False)
    api_error: str = field(default="", compare=False, repr=False)
    travel_time_state: str = field(default=TravelTimeState.DEFAULT, compare=False, repr=False)


@dataclass
class EmptySpace:
    """Free time block between travel end and next visit start."""
    id: Optional[int]
    route_id: int
    from_entry_id: int
    to_entry_id: int
    duration_minutes: int
    start_time: str = ""
    end_time: str = ""


@dataclass
class ExtraTimeBlock:
    """Extra time block shown before a visit (except first visit)."""
    id: Optional[int]
    route_id: int
    to_entry_id: int


@dataclass
class Route:
    """A named route column containing ordered visit entries."""
    id: Optional[int]
    name: str
    notes: str = ""
    display_order: int = 0
    route_color: Optional[str] = None
    # Runtime-only collections (loaded by PersistenceService)
    entries: list[RouteEntry] = field(default_factory=list, compare=False, repr=False)
    travel_segments: list[TravelSegment] = field(default_factory=list, compare=False, repr=False)
    empty_spaces: list[EmptySpace] = field(default_factory=list, compare=False, repr=False)
    extra_time_blocks: list[ExtraTimeBlock] = field(default_factory=list, compare=False, repr=False)

    def sorted_entries(self) -> list[RouteEntry]:
        return sorted(self.entries, key=lambda e: e.position)

    def travel_segment_between(self, from_id: int, to_id: int) -> Optional[TravelSegment]:
        for seg in self.travel_segments:
            if seg.from_entry_id == from_id and seg.to_entry_id == to_id:
                return seg
        return None

    def empty_space_between(self, from_id: int, to_id: int) -> Optional[EmptySpace]:
        for esp in self.empty_spaces:
            if esp.from_entry_id == from_id and esp.to_entry_id == to_id:
                return esp
        return None

    def extra_time_for_entry(self, to_entry_id: int) -> Optional[ExtraTimeBlock]:
        for block in self.extra_time_blocks:
            if block.to_entry_id == to_entry_id:
                return block
        return None


@dataclass
class OfficeTemplate:
    """The reusable office/template visit in the pool panel."""
    id: Optional[int]
    name: str = "Kontor"
    address: str = ""


@dataclass
class Settings:
    """All persisted application settings."""
    default_travel_car: int = 5
    default_travel_bike: int = 5
    default_travel_walk: int = 5
    default_travel_mode: str = "walk"
    minimum_time_between_visits: int = 2
    font_size: int = 12
    api_usage_count: int = 0
    api_usage_limit: int = 10000
    extra_time_minutes: int = 5
    extra_time_auto_place: bool = True
    show_travel_blocks: bool = True
    show_space_blocks: bool = True
    show_extra_time_blocks: bool = True
    debug_mode: bool = False
    file_logging_enabled: bool = True
    file_logging_retention_days: int = 30
    visit_color_blue: str = "#1F99CD"
    visit_color_green: str = "#3DB28D"
    visit_color_pink: str = "#EE229F"
    visit_color_red: str = "#D64545"
    visit_color_orange: str = "#F39C3D"
    visit_color_yellow: str = "#E6C84F"
    visit_color_black: str = "#2B2B2B"

    def default_travel_for_mode(self, mode: str) -> int:
        if mode == TravelMode.CAR:
            return self.default_travel_car
        if mode == TravelMode.BIKE:
            return self.default_travel_bike
        return self.default_travel_walk

    def visit_ribbon_color_map(self) -> dict[str, str]:
        defaults = {
            VisitColor.BLUE: "#1F99CD",
            VisitColor.GREEN: "#3DB28D",
            VisitColor.PINK: "#EE229F",
            VisitColor.RED: "#D64545",
            VisitColor.ORANGE: "#F39C3D",
            VisitColor.YELLOW: "#E6C84F",
            VisitColor.BLACK: "#2B2B2B",
        }
        return {
            VisitColor.BLUE: _normalize_hex_color(self.visit_color_blue, defaults[VisitColor.BLUE]),
            VisitColor.GREEN: _normalize_hex_color(self.visit_color_green, defaults[VisitColor.GREEN]),
            VisitColor.PINK: _normalize_hex_color(self.visit_color_pink, defaults[VisitColor.PINK]),
            VisitColor.RED: _normalize_hex_color(self.visit_color_red, defaults[VisitColor.RED]),
            VisitColor.ORANGE: _normalize_hex_color(self.visit_color_orange, defaults[VisitColor.ORANGE]),
            VisitColor.YELLOW: _normalize_hex_color(self.visit_color_yellow, defaults[VisitColor.YELLOW]),
            VisitColor.BLACK: _normalize_hex_color(self.visit_color_black, defaults[VisitColor.BLACK]),
        }
