"""
RouteRecalculationEngine
========================
Recalculates all TravelSegments and EmptySpaces in a route after any mutation.
Rules:
    - Segments are contiguous with half-open semantics [start, end).
        Example: 07:00–07:30 means 30 minutes.
        Next block starts exactly at 07:30.
  - Same address ⇒ travel_minutes = 0 (no travel block displayed).
  - If next_visit.start < travel_end ⇒ cascade-push next visit forward.
    - EmptySpace blocks are explicit/manual only.
        They are not auto-created from incidental timing differences.
"""

from __future__ import annotations

from domain.models import (
    Route,
    RouteEntry,
    TravelSegment,
    EmptySpace,
    TravelMode,
    TravelTimeState,
    Settings,
)
from services.persistence_service import PersistenceService
from services.travel_time_service import TravelTimeService


def _t2m(hhmm: str) -> int:
    """Time string → absolute minutes.

    Accepts either HH:MM (day 0) or "D<day> HH:MM".
    """
    try:
        text = (hhmm or "").strip()
        day = 0
        if text.startswith("D") and " " in text:
            day_part, text = text.split(" ", 1)
            day = int(day_part[1:])
        h, m = text.split(":")
        return day * 1440 + int(h) * 60 + int(m)
    except Exception:
        return 0


def _m2t(minutes: int) -> str:
    """Absolute minutes → HH:MM or D<day> HH:MM."""
    minutes = max(0, minutes)
    day = minutes // 1440
    rem = minutes % 1440
    hhmm = f"{rem // 60:02d}:{rem % 60:02d}"
    if day <= 0:
        return hhmm
    return f"D{day} {hhmm}"


def _display_time(time_value: str) -> str:
    """Return display HH:MM for any internal time representation."""
    return _m2t(_t2m(time_value)).split(" ")[-1]


class RouteRecalculationEngine:
    def __init__(self, persistence: PersistenceService,
                 travel_service: TravelTimeService,
                 settings: Settings):
        self._db = persistence
        self._travel = travel_service
        self._settings = settings

    def set_settings(self, settings: Settings):
        self._settings = settings

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
            self._prune_stale(route, entries)
            self._persist(route)
            return

        for i in range(len(entries) - 1):
            e_from = entries[i]
            e_to = entries[i + 1]

            from_addr = e_from.api_address
            to_addr = e_to.api_address
            mode = self._get_mode(route, e_from.id, e_to.id)
            seg = route.travel_segment_between(e_from.id, e_to.id)
            travel_min = self._resolve_pair_travel_minutes(
                e_from, e_to, seg, mode, from_addr, to_addr
            )

            e_from_end = _t2m(e_from.end_time)
            travel_start = e_from_end
            travel_end = travel_start + travel_min
            esp = route.empty_space_between(e_from.id, e_to.id)
            manual_space = max(0, esp.duration_minutes) if esp else 0
            extra = route.extra_time_for_entry(e_to.id)
            extra_minutes = self._settings.extra_time_minutes if extra else 0
            target_to_start = travel_end + extra_minutes + manual_space

            duration = _t2m(e_to.end_time) - _t2m(e_to.start_time)
            if _t2m(e_to.start_time) != target_to_start:
                e_to.start_time = _m2t(target_to_start)
                e_to.end_time = _m2t(target_to_start + duration)

            # Update or create TravelSegment
            if seg is None:
                seg = TravelSegment(
                    id=None, route_id=route.id,
                    from_entry_id=e_from.id, to_entry_id=e_to.id,
                    mode=mode, travel_minutes=travel_min,
                )
                route.travel_segments.append(seg)
            else:
                if not seg.is_custom and not seg.is_calculating:
                    seg.travel_minutes = travel_min
                    if seg.travel_time_state == TravelTimeState.CALCULATED:
                        seg.calculated_minutes = travel_min
            seg.start_time = _m2t(travel_start)
            seg.end_time = _m2t(travel_end)

            if esp is not None:
                esp.duration_minutes = manual_space
                esp.start_time = _m2t(travel_end)
                esp.end_time = _m2t(travel_end + manual_space)

        # Remove stale segments/spaces for pairs that no longer exist
        self._prune_stale(route, entries)
        self._persist(route)

    def shift_following_entries(self, route: Route, anchor_entry_id: int,
                                delta_minutes: int,
                                include_anchor: bool = False) -> None:
        """Shift the time of all entries after anchor by delta minutes.

        Positive delta shifts later; negative shifts earlier.
        """
        if delta_minutes == 0:
            return
        entries = route.sorted_entries()
        idx = next((i for i, e in enumerate(entries) if e.id == anchor_entry_id), None)
        if idx is None:
            return
        start_idx = idx if include_anchor else idx + 1
        for i in range(start_idx, len(entries)):
            e = entries[i]
            duration = _t2m(e.end_time) - _t2m(e.start_time)
            new_start = max(0, _t2m(e.start_time) + delta_minutes)
            e.start_time = _m2t(new_start)
            e.end_time = _m2t(new_start + duration)

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
            seg = route.travel_segment_between(e_from.id, e_to.id)
            travel_min = self._resolve_pair_travel_minutes(
                e_from, e_to, seg, mode, from_addr, to_addr
            )

            e_from_end = _t2m(e_from.end_time)
            travel_start = e_from_end
            travel_end = travel_start + travel_min
            extra = route.extra_time_for_entry(e_to.id)
            extra_minutes = self._settings.extra_time_minutes if extra else 0
            earliest_to_start = travel_end + extra_minutes
            e_to_start = _t2m(e_to.start_time)

            if e_to_start < earliest_to_start:
                duration = _t2m(e_to.end_time) - _t2m(e_to.start_time)
                e_to.start_time = _m2t(earliest_to_start)
                e_to.end_time = _m2t(earliest_to_start + duration)
                e_to_start = earliest_to_start

            empty_duration = max(0, e_to_start - earliest_to_start)

            if seg:
                if not seg.is_custom and not seg.is_calculating:
                    seg.travel_minutes = travel_min
                    if seg.travel_time_state == TravelTimeState.CALCULATED:
                        seg.calculated_minutes = travel_min
                seg.start_time = _m2t(travel_start)
                seg.end_time = _m2t(travel_end)

            esp = route.empty_space_between(e_from.id, e_to.id)
            if esp:
                esp.duration_minutes = empty_duration
                esp.start_time = _m2t(earliest_to_start)
                esp.end_time = _m2t(earliest_to_start + empty_duration)

        self._persist(route)

    def add_entry_to_route(self, route: Route, entry: RouteEntry,
                           default_mode: str, insert_index: int | None = None) -> None:
        """
        Place a new entry in route. If insert_index is omitted, append.
        """
        entries = route.sorted_entries()
        if insert_index is None:
            insert_index = len(entries)
        insert_index = max(0, min(insert_index, len(entries)))

        duration = max(1, _t2m(entry.end_time) - _t2m(entry.start_time))
        if insert_index < len(entries):
            # Insert *between* visits at the current start time of the target slot.
            # Placement ignores existing travel/space internals and is based only
            # on between-visit position.
            new_start = _t2m(entries[insert_index].start_time)
        elif insert_index > 0:
            prev = entries[insert_index - 1]
            from_addr = prev.api_address
            to_addr = entry.api_address
            if from_addr and to_addr and (
                    from_addr == to_addr or
                    prev.display_address == entry.display_address):
                travel_min = 0
            else:
                cached = self._travel.get_travel_minutes(from_addr, to_addr, default_mode)
                travel_min = cached if cached is not None else 0
            new_start = _t2m(prev.end_time) + travel_min
        else:
            new_start = _t2m(entry.start_time)

        entry.start_time = _m2t(new_start)
        entry.end_time = _m2t(new_start + duration)
        entry.position = insert_index
        entry.route_id = route.id

        # Shift existing positions at/after insert slot to avoid position collisions.
        for existing in entries:
            if existing.position >= insert_index:
                existing.position += 1

        self._db.add_route_entry(entry)
        route.entries.append(entry)

        # Re-number positions
        for pos, existing in enumerate(route.sorted_entries()):
            existing.position = pos

        # Create default travel segment and empty space for the new pair
        ordered = route.sorted_entries()
        idx = next((i for i, e in enumerate(ordered) if e.id == entry.id), None)
        if idx is not None and idx > 0:
            last = ordered[idx - 1]
            seg = TravelSegment(
                id=None, route_id=route.id,
                from_entry_id=last.id, to_entry_id=entry.id,
                mode=default_mode, travel_minutes=0, is_custom=False,
            )
            seg.is_calculating = True
            route.travel_segments.append(seg)
            esp = EmptySpace(
                id=None, route_id=route.id,
                from_entry_id=last.id, to_entry_id=entry.id,
                duration_minutes=0,
            )
            route.empty_spaces.append(esp)
        if idx is not None and idx < len(ordered) - 1:
            nxt = ordered[idx + 1]
            seg = TravelSegment(
                id=None, route_id=route.id,
                from_entry_id=entry.id, to_entry_id=nxt.id,
                mode=default_mode, travel_minutes=0, is_custom=False,
            )
            seg.is_calculating = True
            route.travel_segments.append(seg)
            esp = EmptySpace(
                id=None, route_id=route.id,
                from_entry_id=entry.id, to_entry_id=nxt.id,
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
        old = max(0, seg.travel_minutes)
        seg.travel_minutes = minutes
        seg.is_custom = True
        delta = max(0, minutes) - old
        self.shift_following_entries(route, seg.to_entry_id, delta, include_anchor=True)
        self.recalculate(route)

    def restore_calculated_travel(self, route: Route, seg: TravelSegment) -> None:
        old = max(0, seg.travel_minutes)
        restored = seg.calculated_minutes
        if restored is None:
            restored = self._calculate_minutes_for_segment(route, seg)
            if restored is not None:
                seg.calculated_minutes = restored

        if restored is not None:
            seg.travel_minutes = max(0, restored)
            seg.is_custom = False
        delta = max(0, seg.travel_minutes) - old
        self.shift_following_entries(route, seg.to_entry_id, delta, include_anchor=True)
        self.recalculate(route)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_mode(self, route: Route, from_id: int, to_id: int) -> str:
        seg = route.travel_segment_between(from_id, to_id)
        return seg.mode if seg else TravelMode.CAR

    def _calculate_minutes_for_segment(self, route: Route,
                                       seg: TravelSegment) -> int | None:
        from_entry = next((e for e in route.entries if e.id == seg.from_entry_id), None)
        to_entry = next((e for e in route.entries if e.id == seg.to_entry_id), None)
        if not from_entry or not to_entry:
            return None

        from_addr = from_entry.api_address
        to_addr = to_entry.api_address
        if from_addr and to_addr and (
                from_addr == to_addr or
                from_entry.display_address == to_entry.display_address):
            return 0

        cached = self._travel.get_travel_minutes(from_addr, to_addr, seg.mode)
        if cached is not None:
            return cached
        return self._travel.get_default_minutes(seg.mode)

    def _resolve_pair_travel_minutes(self,
                                     from_entry: RouteEntry,
                                     to_entry: RouteEntry,
                                     seg: TravelSegment | None,
                                     mode: str,
                                     from_addr: str,
                                     to_addr: str) -> int:
        if from_addr and to_addr and (
                from_addr == to_addr or
                from_entry.display_address == to_entry.display_address):
            return 0

        if seg is not None:
            if seg.is_custom:
                return max(0, int(seg.travel_minutes))
            if seg.is_calculating:
                return max(0, int(seg.travel_minutes))

        cached = self._travel.get_travel_minutes(from_addr, to_addr, mode)
        if cached is not None:
            return cached
        if seg is not None:
            return max(0, int(seg.travel_minutes))
        return self._travel.get_default_minutes(mode)

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
            if (s.from_entry_id, s.to_entry_id) in valid_pairs and s.duration_minutes > 0
        ]
        valid_to_ids = {entry.id for entry in current_entries}
        route.extra_time_blocks = [
            b for b in route.extra_time_blocks
            if b.to_entry_id in valid_to_ids
        ]

    def _persist(self, route: Route):
        """Write all entries, segments, spaces to DB."""
        self._db.update_all_entries_for_route(route)
        self._db.delete_segments_for_route(route.id)
        self._db.delete_empty_spaces_for_route(route.id)
        self._db.delete_extra_time_blocks_for_route(route.id)
        for seg in route.travel_segments:
            seg.id = None
            self._db.upsert_travel_segment(seg)
        for esp in route.empty_spaces:
            esp.id = None
            self._db.upsert_empty_space(esp)
        for block in route.extra_time_blocks:
            block.id = None
            self._db.upsert_extra_time_block(block)
