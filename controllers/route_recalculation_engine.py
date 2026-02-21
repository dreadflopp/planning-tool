"""
RouteRecalculationEngine
========================
Recalculates all TravelSegments and EmptySpaces in a route after any mutation.
Rules:
  - Segments are contiguous: travel starts at entry.end_time.
  - Same address ⇒ travel_minutes = 0 (no travel block displayed).
  - If next_visit.start < travel_end ⇒ cascade-push next visit forward.
  - empty_duration = next_visit.start - travel_end  (may be 0, never negative).
  - 'Apply Minimum Time' shifts start/end times (preserving duration) so that
    (travel + empty) >= min_time for every gap.
"""

from __future__ import annotations

from domain.models import Route, RouteEntry, TravelSegment, EmptySpace, TravelMode
from services.persistence_service import PersistenceService
from services.travel_time_service import TravelTimeService


def _t2m(hhmm: str) -> int:
    """HH:MM → minutes since midnight."""
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _m2t(minutes: int) -> str:
    """Minutes since midnight → HH:MM (clamped 0–1439)."""
    minutes = max(0, min(1439, minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class RouteRecalculationEngine:
    def __init__(self, persistence: PersistenceService,
                 travel_service: TravelTimeService):
        self._db = persistence
        self._travel = travel_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recalculate(self, route: Route) -> None:
        """
        Fully recalculate travel segments and empty spaces for *route*.
        Modifies route.travel_segments, route.empty_spaces, and entry times
        in place.  Persists everything to DB.
        """
        entries = route.sorted_entries()
        if len(entries) < 2:
            self._persist(route)
            return

        for i in range(len(entries) - 1):
            e_from = entries[i]
            e_to = entries[i + 1]

            from_addr = e_from.api_address
            to_addr = e_to.api_address
            mode = self._get_mode(route, e_from.id, e_to.id)

            if from_addr and to_addr and (
                    from_addr == to_addr or
                    e_from.display_address == e_to.display_address):
                travel_min = 0
            else:
                cached = self._travel.get_travel_minutes(from_addr, to_addr, mode)
                travel_min = (
                    cached if cached is not None
                    else self._travel.get_default_minutes(mode)
                )

            e_from_end = _t2m(e_from.end_time)
            travel_start = e_from_end
            travel_end = e_from_end + travel_min

            e_to_start = _t2m(e_to.start_time)

            # Cascade: push next entry if it starts before travel ends
            if e_to_start < travel_end:
                duration = _t2m(e_to.end_time) - _t2m(e_to.start_time)
                e_to.start_time = _m2t(travel_end)
                e_to.end_time = _m2t(travel_end + duration)
                e_to_start = travel_end

            empty_duration = max(0, e_to_start - travel_end)

            # Update or create TravelSegment
            seg = route.travel_segment_between(e_from.id, e_to.id)
            if seg is None:
                seg = TravelSegment(
                    id=None, route_id=route.id,
                    from_entry_id=e_from.id, to_entry_id=e_to.id,
                    mode=mode, travel_minutes=travel_min,
                )
                route.travel_segments.append(seg)
            else:
                if not seg.is_custom:
                    seg.travel_minutes = travel_min
            seg.start_time = _m2t(travel_start)
            seg.end_time = _m2t(travel_end)

            # Update or create EmptySpace
            esp = route.empty_space_between(e_from.id, e_to.id)
            if esp is None:
                esp = EmptySpace(
                    id=None, route_id=route.id,
                    from_entry_id=e_from.id, to_entry_id=e_to.id,
                    duration_minutes=empty_duration,
                )
                route.empty_spaces.append(esp)
            else:
                esp.duration_minutes = empty_duration
            esp.start_time = _m2t(travel_end)
            esp.end_time = _m2t(travel_end + empty_duration)

        # Remove stale segments/spaces for pairs that no longer exist
        self._prune_stale(route, entries)
        self._persist(route)

    def recalculate_after_entry_time_change(self, route: Route,
                                             changed_entry: RouteEntry) -> None:
        """
        Cascade time changes forward from *changed_entry*.
        The entry's new start/end times are already set by the caller.
        """
        entries = route.sorted_entries()
        idx = next((i for i, e in enumerate(entries) if e.id == changed_entry.id), None)
        if idx is None:
            return

        # Cascade forward from this entry
        for i in range(idx, len(entries) - 1):
            e_from = entries[i]
            e_to = entries[i + 1]
            from_addr = e_from.api_address
            to_addr = e_to.api_address
            mode = self._get_mode(route, e_from.id, e_to.id)

            if from_addr and to_addr and (
                    from_addr == to_addr or
                    e_from.display_address == e_to.display_address):
                travel_min = 0
            else:
                cached = self._travel.get_travel_minutes(from_addr, to_addr, mode)
                travel_min = (
                    cached if cached is not None
                    else self._travel.get_default_minutes(mode)
                )

            e_from_end = _t2m(e_from.end_time)
            travel_end = e_from_end + travel_min
            e_to_start = _t2m(e_to.start_time)

            if e_to_start < travel_end:
                duration = _t2m(e_to.end_time) - _t2m(e_to.start_time)
                e_to.start_time = _m2t(travel_end)
                e_to.end_time = _m2t(travel_end + duration)
                e_to_start = travel_end

            empty_duration = max(0, e_to_start - travel_end)

            seg = route.travel_segment_between(e_from.id, e_to.id)
            if seg:
                if not seg.is_custom:
                    seg.travel_minutes = travel_min
                seg.start_time = _m2t(e_from_end)
                seg.end_time = _m2t(travel_end)

            esp = route.empty_space_between(e_from.id, e_to.id)
            if esp:
                esp.duration_minutes = empty_duration
                esp.start_time = _m2t(travel_end)
                esp.end_time = _m2t(travel_end + empty_duration)

        self._persist(route)

    def apply_minimum_time(self, route: Route, min_time: int) -> None:
        """
        Shift entry start/end times so that (travel + empty) >= min_time
        for every consecutive pair. Cascades forward through all entries.
        """
        entries = route.sorted_entries()
        for i in range(len(entries) - 1):
            e_from = entries[i]
            e_to = entries[i + 1]

            seg = route.travel_segment_between(e_from.id, e_to.id)
            travel_min = seg.travel_minutes if seg else 0

            effective_min = max(travel_min, min_time)
            e_from_end = _t2m(e_from.end_time)
            e_to_start = _t2m(e_to.start_time)
            current_gap = e_to_start - e_from_end

            if current_gap < effective_min:
                shift = effective_min - current_gap
                for j in range(i + 1, len(entries)):
                    duration = _t2m(entries[j].end_time) - _t2m(entries[j].start_time)
                    entries[j].start_time = _m2t(_t2m(entries[j].start_time) + shift)
                    entries[j].end_time = _m2t(_t2m(entries[j].start_time) + duration)

        self.recalculate(route)

    def strip_extra_empty_space(self, route: Route) -> None:
        """
        Remove all empty space from a route by collapsing each gap to just
        the travel time. Each entry is moved as early as possible.
        """
        entries = route.sorted_entries()
        for i in range(1, len(entries)):
            e_prev = entries[i - 1]
            e_curr = entries[i]
            seg = route.travel_segment_between(e_prev.id, e_curr.id)
            travel_min = seg.travel_minutes if seg else 0

            new_start = _t2m(e_prev.end_time) + travel_min
            duration = _t2m(e_curr.end_time) - _t2m(e_curr.start_time)
            e_curr.start_time = _m2t(new_start)
            e_curr.end_time = _m2t(new_start + duration)

        self.recalculate(route)

    def add_entry_to_route(self, route: Route, entry: RouteEntry,
                           default_mode: str) -> None:
        """
        Place a new entry at the end of the route. Sets start time to
        the next available minute after the last item.
        """
        entries = route.sorted_entries()
        if entries:
            last = entries[-1]
            last_seg = route.travel_segment_between(last.id, None)
            # Calculate earliest possible start
            last_end = _t2m(last.end_time)
            from_addr = last.api_address
            to_addr = entry.api_address
            if from_addr and to_addr and (
                    from_addr == to_addr or
                    last.display_address == entry.display_address):
                travel_min = 0
            else:
                cached = self._travel.get_travel_minutes(from_addr, to_addr, default_mode)
                travel_min = (
                    cached if cached is not None
                    else self._travel.get_default_minutes(default_mode)
                )
            new_start = last_end + travel_min
            duration = _t2m(entry.end_time) - _t2m(entry.start_time)
            entry.start_time = _m2t(new_start)
            entry.end_time = _m2t(new_start + duration)
        entry.position = len(entries)
        entry.route_id = route.id
        self._db.add_route_entry(entry)
        route.entries.append(entry)

        # Create default travel segment and empty space for the new pair
        if entries:
            last = entries[-1]
            seg = TravelSegment(
                id=None, route_id=route.id,
                from_entry_id=last.id, to_entry_id=entry.id,
                mode=default_mode, travel_minutes=0,
            )
            route.travel_segments.append(seg)
            esp = EmptySpace(
                id=None, route_id=route.id,
                from_entry_id=last.id, to_entry_id=entry.id,
                duration_minutes=0,
            )
            route.empty_spaces.append(esp)

        self.recalculate(route)

    def remove_entry_from_route(self, route: Route, entry: RouteEntry,
                                 replace_with_empty: bool = True) -> None:
        """
        Remove an entry from a route. If replace_with_empty, the slot is
        replaced with an EmptySpace of equal duration.  Otherwise it just
        closes the gap (cascade).
        """
        entries = route.sorted_entries()
        idx = next((i for i, e in enumerate(entries) if e.id == entry.id), None)
        if idx is None:
            return

        entry_duration = _t2m(entry.end_time) - _t2m(entry.start_time)

        # Remove segments/spaces involving this entry
        route.travel_segments = [
            s for s in route.travel_segments
            if s.from_entry_id != entry.id and s.to_entry_id != entry.id
        ]
        route.empty_spaces = [
            s for s in route.empty_spaces
            if s.from_entry_id != entry.id and s.to_entry_id != entry.id
        ]
        self._db.delete_route_entry(entry.id)
        route.entries.remove(entry)

        # Re-number positions
        for i, e in enumerate(route.sorted_entries()):
            e.position = i

        # Insert bridging segment/space between the surrounding entries
        if replace_with_empty and 0 < idx < len(route.entries):
            e_prev = route.sorted_entries()[idx - 1]
            e_next = route.sorted_entries()[idx]
            seg = TravelSegment(
                id=None, route_id=route.id,
                from_entry_id=e_prev.id, to_entry_id=e_next.id,
                mode=TravelMode.CAR, travel_minutes=0,
            )
            route.travel_segments.append(seg)
            # The empty space should absorb the removed slot's time
            esp = EmptySpace(
                id=None, route_id=route.id,
                from_entry_id=e_prev.id, to_entry_id=e_next.id,
                duration_minutes=entry_duration,
            )
            route.empty_spaces.append(esp)

        self.recalculate(route)

    def swap_entries(self, route: Route, entry_a: RouteEntry,
                     entry_b: RouteEntry) -> None:
        """Swap two adjacent entries. Entry_a moves up (takes entry_b's start time)."""
        pos_a, pos_b = entry_a.position, entry_b.position
        time_b_start = entry_b.start_time
        duration_a = _t2m(entry_a.end_time) - _t2m(entry_a.start_time)

        entry_a.position, entry_b.position = pos_b, pos_a
        entry_a.start_time = time_b_start
        entry_a.end_time = _m2t(_t2m(time_b_start) + duration_a)

        self.recalculate(route)

    def update_travel_segment_mode(self, route: Route, seg: TravelSegment,
                                    new_mode: str) -> None:
        seg.mode = new_mode
        seg.is_custom = False
        self.recalculate(route)

    def set_custom_travel_minutes(self, route: Route, seg: TravelSegment,
                                   minutes: int) -> None:
        seg.travel_minutes = minutes
        seg.is_custom = True
        self.recalculate(route)

    def restore_calculated_travel(self, route: Route, seg: TravelSegment) -> None:
        if seg.calculated_minutes is not None:
            seg.travel_minutes = seg.calculated_minutes
            seg.is_custom = False
        self.recalculate(route)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_mode(self, route: Route, from_id: int, to_id: int) -> str:
        seg = route.travel_segment_between(from_id, to_id)
        return seg.mode if seg else TravelMode.CAR

    def _prune_stale(self, route: Route, current_entries: list[RouteEntry]):
        valid_pairs = {
            (current_entries[i].id, current_entries[i + 1].id)
            for i in range(len(current_entries) - 1)
        }
        route.travel_segments = [
            s for s in route.travel_segments
            if (s.from_entry_id, s.to_entry_id) in valid_pairs
        ]
        route.empty_spaces = [
            s for s in route.empty_spaces
            if (s.from_entry_id, s.to_entry_id) in valid_pairs
        ]

    def _persist(self, route: Route):
        """Write all entries, segments, spaces to DB."""
        self._db.update_all_entries_for_route(route)
        self._db.delete_segments_for_route(route.id)
        self._db.delete_empty_spaces_for_route(route.id)
        for seg in route.travel_segments:
            seg.id = None
            self._db.upsert_travel_segment(seg)
        for esp in route.empty_spaces:
            esp.id = None
            self._db.upsert_empty_space(esp)
