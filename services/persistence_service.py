"""SQLite persistence layer – schema creation and all CRUD operations."""

import json
import os
import sqlite3
import sys
from typing import Optional

from domain.models import (
    Visit, Route, RouteEntry, TravelSegment, EmptySpace,
    OfficeTemplate, Settings, TravelMode, ExtraTimeBlock, TravelTimeState,
)
from domain.constants import DB_FILENAME


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS visits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id     TEXT    UNIQUE NOT NULL,
    name          TEXT    NOT NULL,
    address       TEXT    NOT NULL,
    street        TEXT    NOT NULL,
    default_start TEXT    NOT NULL,
    default_end   TEXT    NOT NULL,
    insatser      TEXT    NOT NULL DEFAULT '',
    color         TEXT,
    raw_data      TEXT    NOT NULL DEFAULT '{}',
    full_address  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS routes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    notes        TEXT    NOT NULL DEFAULT '',
    display_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS route_visit_order (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id          INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    visit_id          INTEGER REFERENCES visits(id) ON DELETE SET NULL,
    position          INTEGER NOT NULL,
    start_time        TEXT    NOT NULL,
    end_time          TEXT    NOT NULL,
    is_office_instance INTEGER NOT NULL DEFAULT 0,
    office_name       TEXT    NOT NULL DEFAULT '',
    office_address    TEXT    NOT NULL DEFAULT '',
    office_color      TEXT    NOT NULL DEFAULT 'black'
);

CREATE TABLE IF NOT EXISTS travel_segments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id            INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    from_entry_id       INTEGER NOT NULL REFERENCES route_visit_order(id) ON DELETE CASCADE,
    to_entry_id         INTEGER NOT NULL REFERENCES route_visit_order(id) ON DELETE CASCADE,
    mode                TEXT    NOT NULL DEFAULT 'car',
    travel_minutes      INTEGER NOT NULL DEFAULT 15,
    is_custom           INTEGER NOT NULL DEFAULT 0,
    calculated_minutes  INTEGER,
    travel_time_state   TEXT    NOT NULL DEFAULT 'default',
    UNIQUE(from_entry_id, to_entry_id)
);

CREATE TABLE IF NOT EXISTS empty_spaces (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id        INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    from_entry_id   INTEGER NOT NULL REFERENCES route_visit_order(id) ON DELETE CASCADE,
    to_entry_id     INTEGER NOT NULL REFERENCES route_visit_order(id) ON DELETE CASCADE,
    duration_minutes INTEGER NOT NULL DEFAULT 0,
    UNIQUE(from_entry_id, to_entry_id)
);

CREATE TABLE IF NOT EXISTS extra_time_blocks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id        INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    to_entry_id     INTEGER NOT NULL REFERENCES route_visit_order(id) ON DELETE CASCADE,
    UNIQUE(route_id, to_entry_id)
);

CREATE TABLE IF NOT EXISTS travel_time_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    from_address  TEXT    NOT NULL,
    to_address    TEXT    NOT NULL,
    mode          TEXT    NOT NULL,
    travel_minutes INTEGER NOT NULL,
    calculated_at TEXT    NOT NULL,
    UNIQUE(from_address, to_address, mode)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS column_order (
    column_type  TEXT NOT NULL,
    column_id    TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    PRIMARY KEY (column_type, column_id)
);

CREATE TABLE IF NOT EXISTS import_metadata (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at    TEXT NOT NULL,
    filename       TEXT NOT NULL,
    total_rows     INTEGER NOT NULL,
    imported_count INTEGER NOT NULL,
    skipped_count  INTEGER NOT NULL,
    duplicate_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS office_template (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL DEFAULT 'Kontor',
    address TEXT NOT NULL DEFAULT ''
);

INSERT OR IGNORE INTO office_template(id, name, address) VALUES (1, 'Kontor', '');
"""


class PersistenceService:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            if getattr(sys, "frozen", False):
                app_dir = os.path.dirname(os.path.abspath(sys.executable))
            else:
                app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(app_dir, DB_FILENAME)
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL;")
        self._create_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_schema(self):
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self):
        """Apply incremental schema migrations for existing databases."""
        cur = self._conn.execute("PRAGMA table_info(visits)")
        existing = {row[1] for row in cur.fetchall()}
        if "full_address" not in existing:
            self._conn.execute(
                "ALTER TABLE visits ADD COLUMN full_address TEXT NOT NULL DEFAULT ''"
            )

        cur = self._conn.execute("PRAGMA table_info(route_visit_order)")
        existing_route_cols = {row[1] for row in cur.fetchall()}
        if "office_color" not in existing_route_cols:
            self._conn.execute(
                "ALTER TABLE route_visit_order ADD COLUMN office_color TEXT NOT NULL DEFAULT 'black'"
            )

        cur = self._conn.execute("PRAGMA table_info(travel_segments)")
        existing_seg_cols = {row[1] for row in cur.fetchall()}
        if "travel_time_state" not in existing_seg_cols:
            self._conn.execute(
                "ALTER TABLE travel_segments ADD COLUMN travel_time_state TEXT NOT NULL DEFAULT 'default'"
            )

        cur = self._conn.execute("PRAGMA table_info(extra_time_blocks)")
        if not cur.fetchall():
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS extra_time_blocks (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    route_id        INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
                    to_entry_id     INTEGER NOT NULL REFERENCES route_visit_order(id) ON DELETE CASCADE,
                    UNIQUE(route_id, to_entry_id)
                )
                """
            )

    def close(self):
        self._conn.close()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def load_settings(self) -> Settings:
        s = Settings()
        cur = self._conn.execute("SELECT key, value FROM settings")
        for row in cur.fetchall():
            k, v = row["key"], row["value"]
            if k == "default_travel_car":
                s.default_travel_car = int(v)
            elif k == "default_travel_bike":
                s.default_travel_bike = int(v)
            elif k == "default_travel_walk":
                s.default_travel_walk = int(v)
            elif k == "default_travel_mode":
                s.default_travel_mode = v
            elif k == "minimum_time_between_visits":
                s.minimum_time_between_visits = int(v)
            elif k == "font_size":
                s.font_size = int(v)
            elif k == "api_usage_count":
                s.api_usage_count = int(v)
            elif k == "api_usage_limit":
                s.api_usage_limit = int(v)
            elif k == "extra_time_minutes":
                s.extra_time_minutes = int(v)
            elif k == "extra_time_auto_place":
                s.extra_time_auto_place = str(v).strip().lower() in {"1", "true", "yes", "on"}
            elif k == "show_travel_blocks":
                s.show_travel_blocks = str(v).strip().lower() in {"1", "true", "yes", "on"}
            elif k == "show_space_blocks":
                s.show_space_blocks = str(v).strip().lower() in {"1", "true", "yes", "on"}
            elif k == "show_extra_time_blocks":
                s.show_extra_time_blocks = str(v).strip().lower() in {"1", "true", "yes", "on"}
            elif k == "debug_mode":
                s.debug_mode = str(v).strip().lower() in {"1", "true", "yes", "on"}
            elif k == "file_logging_enabled":
                s.file_logging_enabled = str(v).strip().lower() in {"1", "true", "yes", "on"}
            elif k == "file_logging_retention_days":
                try:
                    s.file_logging_retention_days = max(1, int(v))
                except Exception:
                    s.file_logging_retention_days = 30
            elif k == "visit_color_blue":
                s.visit_color_blue = str(v)
            elif k == "visit_color_green":
                s.visit_color_green = str(v)
            elif k == "visit_color_pink":
                s.visit_color_pink = str(v)
            elif k == "visit_color_red":
                s.visit_color_red = str(v)
            elif k == "visit_color_orange":
                s.visit_color_orange = str(v)
            elif k == "visit_color_yellow":
                s.visit_color_yellow = str(v)
            elif k == "visit_color_black":
                s.visit_color_black = str(v)
        return s

    def save_settings(self, s: Settings):
        rows = [
            ("default_travel_car", str(s.default_travel_car)),
            ("default_travel_bike", str(s.default_travel_bike)),
            ("default_travel_walk", str(s.default_travel_walk)),
            ("default_travel_mode", s.default_travel_mode),
            ("minimum_time_between_visits", str(s.minimum_time_between_visits)),
            ("font_size", str(s.font_size)),
            ("api_usage_count", str(s.api_usage_count)),
            ("api_usage_limit", str(s.api_usage_limit)),
            ("extra_time_minutes", str(s.extra_time_minutes)),
            ("extra_time_auto_place", "1" if s.extra_time_auto_place else "0"),
            ("show_travel_blocks", "1" if s.show_travel_blocks else "0"),
            ("show_space_blocks", "1" if s.show_space_blocks else "0"),
            ("show_extra_time_blocks", "1" if s.show_extra_time_blocks else "0"),
            ("debug_mode", "1" if s.debug_mode else "0"),
            ("file_logging_enabled", "1" if s.file_logging_enabled else "0"),
            ("file_logging_retention_days", str(max(1, int(s.file_logging_retention_days)))),
            ("visit_color_blue", str(s.visit_color_blue)),
            ("visit_color_green", str(s.visit_color_green)),
            ("visit_color_pink", str(s.visit_color_pink)),
            ("visit_color_red", str(s.visit_color_red)),
            ("visit_color_orange", str(s.visit_color_orange)),
            ("visit_color_yellow", str(s.visit_color_yellow)),
            ("visit_color_black", str(s.visit_color_black)),
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", rows
        )
        self._conn.commit()

    def increment_api_usage(self) -> int:
        self._conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) "
            "VALUES ('api_usage_count', "
            "CAST(COALESCE((SELECT value FROM settings WHERE key='api_usage_count'), '0') AS INTEGER) + 1)"
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key='api_usage_count'"
        ).fetchone()
        return int(row["value"]) if row else 0

    # ------------------------------------------------------------------
    # Office template
    # ------------------------------------------------------------------

    def load_office_template(self) -> OfficeTemplate:
        row = self._conn.execute("SELECT * FROM office_template WHERE id=1").fetchone()
        if row:
            return OfficeTemplate(id=row["id"], name=row["name"], address=row["address"])
        return OfficeTemplate(id=1)

    def save_office_template(self, tmpl: OfficeTemplate):
        self._conn.execute(
            "INSERT OR REPLACE INTO office_template(id, name, address) VALUES (1, ?, ?)",
            (tmpl.name, tmpl.address),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Visits
    # ------------------------------------------------------------------

    def load_all_visits(self) -> list[Visit]:
        rows = self._conn.execute("SELECT * FROM visits ORDER BY id").fetchall()
        return [self._row_to_visit(r) for r in rows]

    def _row_to_visit(self, row) -> Visit:
        return Visit(
            id=row["id"],
            object_id=row["object_id"],
            name=row["name"],
            address=row["address"],
            street=row["street"],
            default_start=row["default_start"],
            default_end=row["default_end"],
            insatser=row["insatser"],
            color=row["color"],
            raw_data=row["raw_data"],
            full_address=row["full_address"] if "full_address" in row.keys() else "",
        )

    def get_visit_by_object_id(self, object_id: str) -> Optional[Visit]:
        row = self._conn.execute(
            "SELECT * FROM visits WHERE object_id=?", (object_id,)
        ).fetchone()
        return self._row_to_visit(row) if row else None

    def upsert_visit(self, v: Visit) -> Visit:
        """Insert or update a visit. Returns visit with id set."""
        cur = self._conn.execute(
            """INSERT INTO visits(object_id, name, address, street, default_start, default_end,
                                  insatser, color, raw_data, full_address)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(object_id) DO UPDATE SET
                 name=excluded.name, address=excluded.address, street=excluded.street,
                 default_start=excluded.default_start, default_end=excluded.default_end,
                 insatser=excluded.insatser, color=excluded.color, raw_data=excluded.raw_data,
                 full_address=excluded.full_address
            """,
            (v.object_id, v.name, v.address, v.street,
             v.default_start, v.default_end, v.insatser, v.color, v.raw_data,
             v.full_address),
        )
        self._conn.commit()
        if v.id is None:
            v.id = cur.lastrowid
        else:
            row = self._conn.execute(
                "SELECT id FROM visits WHERE object_id=?", (v.object_id,)
            ).fetchone()
            if row:
                v.id = row["id"]
        return v

    def delete_visit(self, visit_id: int):
        self._conn.execute("DELETE FROM visits WHERE id=?", (visit_id,))
        self._conn.commit()

    def get_all_object_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT object_id FROM visits").fetchall()
        return {r["object_id"] for r in rows}

    def get_placed_visit_ids(self) -> set[int]:
        """Return visit_ids that are currently placed in any route."""
        rows = self._conn.execute(
            "SELECT DISTINCT visit_id FROM route_visit_order WHERE visit_id IS NOT NULL"
        ).fetchall()
        return {r["visit_id"] for r in rows}

    def save_import_metadata(self, filename: str, total: int,
                              imported: int, skipped: int, duplicates: int):
        from datetime import datetime
        self._conn.execute(
            "INSERT INTO import_metadata(imported_at, filename, total_rows, imported_count, "
            "skipped_count, duplicate_count) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), filename, total, imported, skipped, duplicates),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def load_all_routes(self, visits_by_id: dict[int, Visit]) -> list[Route]:
        rows = self._conn.execute(
            "SELECT * FROM routes ORDER BY display_order, id"
        ).fetchall()
        routes = [self._row_to_route(r) for r in rows]
        for route in routes:
            route.entries = self._load_entries_for_route(route.id, visits_by_id)
            self._load_segments_and_spaces(route)
            route.extra_time_blocks = self._load_extra_time_blocks_for_route(route.id)
        return routes

    def _load_extra_time_blocks_for_route(self, route_id: int) -> list[ExtraTimeBlock]:
        rows = self._conn.execute(
            "SELECT * FROM extra_time_blocks WHERE route_id=?",
            (route_id,),
        ).fetchall()
        return [
            ExtraTimeBlock(
                id=r["id"],
                route_id=r["route_id"],
                to_entry_id=r["to_entry_id"],
            )
            for r in rows
        ]

    def _row_to_route(self, row) -> Route:
        return Route(
            id=row["id"],
            name=row["name"],
            notes=row["notes"],
            display_order=row["display_order"],
        )

    def _load_entries_for_route(self, route_id: int, visits_by_id: dict) -> list[RouteEntry]:
        rows = self._conn.execute(
            "SELECT * FROM route_visit_order WHERE route_id=? ORDER BY position",
            (route_id,),
        ).fetchall()
        entries = []
        for r in rows:
            entry = RouteEntry(
                id=r["id"],
                route_id=r["route_id"],
                visit_id=r["visit_id"],
                position=r["position"],
                start_time=r["start_time"],
                end_time=r["end_time"],
                is_office_instance=bool(r["is_office_instance"]),
                office_name=r["office_name"],
                office_address=r["office_address"],
                office_color=r["office_color"] if "office_color" in r.keys() else "black",
            )
            if entry.visit_id and entry.visit_id in visits_by_id:
                entry.visit = visits_by_id[entry.visit_id]
            entries.append(entry)
        return entries

    def _load_segments_and_spaces(self, route: Route):
        rows = self._conn.execute(
            "SELECT * FROM travel_segments WHERE route_id=?", (route.id,)
        ).fetchall()
        route.travel_segments = [
            TravelSegment(
                id=r["id"], route_id=r["route_id"],
                from_entry_id=r["from_entry_id"], to_entry_id=r["to_entry_id"],
                mode=r["mode"], travel_minutes=r["travel_minutes"],
                is_custom=bool(r["is_custom"]),
                calculated_minutes=r["calculated_minutes"],
                travel_time_state=(
                    r["travel_time_state"]
                    if "travel_time_state" in r.keys() and r["travel_time_state"]
                    else (
                        TravelTimeState.EDITED if bool(r["is_custom"]) else (
                            TravelTimeState.CALCULATED if r["calculated_minutes"] is not None
                            else TravelTimeState.DEFAULT
                        )
                    )
                ),
            )
            for r in rows
        ]
        rows = self._conn.execute(
            "SELECT * FROM empty_spaces WHERE route_id=?", (route.id,)
        ).fetchall()
        route.empty_spaces = [
            EmptySpace(
                id=r["id"], route_id=r["route_id"],
                from_entry_id=r["from_entry_id"], to_entry_id=r["to_entry_id"],
                duration_minutes=r["duration_minutes"],
            )
            for r in rows
        ]

    def create_route(self, name: str, display_order: int) -> Route:
        cur = self._conn.execute(
            "INSERT INTO routes(name, notes, display_order) VALUES (?, '', ?)",
            (name, display_order),
        )
        self._conn.commit()
        return Route(id=cur.lastrowid, name=name, display_order=display_order)

    def save_route(self, route: Route):
        self._conn.execute(
            "UPDATE routes SET name=?, notes=?, display_order=? WHERE id=?",
            (route.name, route.notes, route.display_order, route.id),
        )
        self._conn.commit()

    def delete_route(self, route_id: int):
        self._conn.execute("DELETE FROM routes WHERE id=?", (route_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Route entries
    # ------------------------------------------------------------------

    def add_route_entry(self, entry: RouteEntry) -> RouteEntry:
        cur = self._conn.execute(
            """INSERT INTO route_visit_order
               (route_id, visit_id, position, start_time, end_time,
                                is_office_instance, office_name, office_address, office_color)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry.route_id, entry.visit_id, entry.position,
             entry.start_time, entry.end_time,
                         int(entry.is_office_instance), entry.office_name, entry.office_address,
                         entry.office_color or "black"),
        )
        self._conn.commit()
        entry.id = cur.lastrowid
        return entry

    def update_route_entry(self, entry: RouteEntry):
        self._conn.execute(
            """UPDATE route_visit_order
               SET position=?, start_time=?, end_time=?,
                   office_name=?, office_address=?, office_color=?
               WHERE id=?""",
            (entry.position, entry.start_time, entry.end_time,
             entry.office_name, entry.office_address, entry.office_color or "black", entry.id),
        )
        self._conn.commit()

    def delete_route_entry(self, entry_id: int):
        self._conn.execute(
            "DELETE FROM route_visit_order WHERE id=?", (entry_id,)
        )
        self._conn.commit()

    def update_all_entries_for_route(self, route: Route):
        """Bulk-update positions and times for all entries in a route."""
        for entry in route.entries:
            self._conn.execute(
                "UPDATE route_visit_order SET position=?, start_time=?, end_time=?, office_color=? WHERE id=?",
                (entry.position, entry.start_time, entry.end_time, entry.office_color or "black", entry.id),
            )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Travel segments
    # ------------------------------------------------------------------

    def upsert_travel_segment(self, seg: TravelSegment) -> TravelSegment:
        cur = self._conn.execute(
            """INSERT INTO travel_segments
               (route_id, from_entry_id, to_entry_id, mode, travel_minutes,
                 is_custom, calculated_minutes, travel_time_state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(from_entry_id, to_entry_id) DO UPDATE SET
                 mode=excluded.mode, travel_minutes=excluded.travel_minutes,
                 is_custom=excluded.is_custom,
                  calculated_minutes=excluded.calculated_minutes,
                  travel_time_state=excluded.travel_time_state""",
            (seg.route_id, seg.from_entry_id, seg.to_entry_id,
               seg.mode, seg.travel_minutes, int(seg.is_custom),
               seg.calculated_minutes, seg.travel_time_state),
        )
        self._conn.commit()
        if seg.id is None:
            seg.id = cur.lastrowid
        return seg

    def delete_travel_segment(self, seg_id: int):
        self._conn.execute("DELETE FROM travel_segments WHERE id=?", (seg_id,))
        self._conn.commit()

    def delete_segments_for_route(self, route_id: int):
        self._conn.execute("DELETE FROM travel_segments WHERE route_id=?", (route_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Empty spaces
    # ------------------------------------------------------------------

    def upsert_empty_space(self, esp: EmptySpace) -> EmptySpace:
        cur = self._conn.execute(
            """INSERT INTO empty_spaces
               (route_id, from_entry_id, to_entry_id, duration_minutes)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(from_entry_id, to_entry_id) DO UPDATE SET
                 duration_minutes=excluded.duration_minutes""",
            (esp.route_id, esp.from_entry_id, esp.to_entry_id, esp.duration_minutes),
        )
        self._conn.commit()
        if esp.id is None:
            esp.id = cur.lastrowid
        return esp

    def delete_empty_spaces_for_route(self, route_id: int):
        self._conn.execute("DELETE FROM empty_spaces WHERE route_id=?", (route_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Extra time blocks
    # ------------------------------------------------------------------

    def upsert_extra_time_block(self, block: ExtraTimeBlock) -> ExtraTimeBlock:
        cur = self._conn.execute(
            """INSERT INTO extra_time_blocks(route_id, to_entry_id)
               VALUES (?, ?)
               ON CONFLICT(route_id, to_entry_id) DO UPDATE SET
                 to_entry_id=excluded.to_entry_id""",
            (block.route_id, block.to_entry_id),
        )
        self._conn.commit()
        if block.id is None:
            block.id = cur.lastrowid
        return block

    def delete_extra_time_block(self, route_id: int, to_entry_id: int):
        self._conn.execute(
            "DELETE FROM extra_time_blocks WHERE route_id=? AND to_entry_id=?",
            (route_id, to_entry_id),
        )
        self._conn.commit()

    def delete_extra_time_blocks_for_route(self, route_id: int):
        self._conn.execute("DELETE FROM extra_time_blocks WHERE route_id=?", (route_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Travel time cache
    # ------------------------------------------------------------------

    def get_cached_travel(self, from_addr: str, to_addr: str, mode: str) -> Optional[int]:
        row = self._conn.execute(
            "SELECT travel_minutes FROM travel_time_cache "
            "WHERE from_address=? AND to_address=? AND mode=?",
            (from_addr, to_addr, mode),
        ).fetchone()
        return row["travel_minutes"] if row else None

    def set_cached_travel(self, from_addr: str, to_addr: str,
                          mode: str, minutes: int):
        from datetime import datetime
        self._conn.execute(
            """INSERT INTO travel_time_cache
               (from_address, to_address, mode, travel_minutes, calculated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(from_address, to_address, mode) DO UPDATE SET
                 travel_minutes=excluded.travel_minutes,
                 calculated_at=excluded.calculated_at""",
            (from_addr, to_addr, mode, minutes, datetime.now().isoformat()),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Column order
    # ------------------------------------------------------------------

    def load_column_order(self, column_type: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT column_id, display_order FROM column_order WHERE column_type=?",
            (column_type,),
        ).fetchall()
        return {r["column_id"]: r["display_order"] for r in rows}

    def save_column_order(self, column_type: str, column_id: str, order: int):
        self._conn.execute(
            "INSERT OR REPLACE INTO column_order(column_type, column_id, display_order) "
            "VALUES (?, ?, ?)",
            (column_type, column_id, order),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # State export / import (JSON)
    # ------------------------------------------------------------------

    def export_state(self) -> dict:
        state = {}
        state["visits"] = [
            dict(r) for r in self._conn.execute("SELECT * FROM visits").fetchall()
        ]
        state["routes"] = [
            dict(r) for r in self._conn.execute("SELECT * FROM routes").fetchall()
        ]
        state["route_visit_order"] = [
            dict(r) for r in self._conn.execute("SELECT * FROM route_visit_order").fetchall()
        ]
        state["travel_segments"] = [
            dict(r) for r in self._conn.execute("SELECT * FROM travel_segments").fetchall()
        ]
        state["empty_spaces"] = [
            dict(r) for r in self._conn.execute("SELECT * FROM empty_spaces").fetchall()
        ]
        state["extra_time_blocks"] = [
            dict(r) for r in self._conn.execute("SELECT * FROM extra_time_blocks").fetchall()
        ]
        state["settings"] = [
            dict(r) for r in self._conn.execute("SELECT * FROM settings").fetchall()
        ]
        state["column_order"] = [
            dict(r) for r in self._conn.execute("SELECT * FROM column_order").fetchall()
        ]
        state["office_template"] = [
            dict(r) for r in self._conn.execute("SELECT * FROM office_template").fetchall()
        ]
        return state

    def import_state(self, state: dict):
        """Wipe current data (except cache) and load from state dict."""
        tables = ["empty_spaces", "travel_segments", "route_visit_order",
                  "extra_time_blocks", "routes", "visits", "settings", "column_order", "office_template"]
        for table in tables:
            self._conn.execute(f"DELETE FROM {table}")

        for row in state.get("visits", []):
            row.setdefault("full_address", "")
            self._conn.execute(
                "INSERT INTO visits(id,object_id,name,address,street,"
                "default_start,default_end,insatser,color,raw_data,full_address) "
                "VALUES (:id,:object_id,:name,:address,:street,"
                ":default_start,:default_end,:insatser,:color,:raw_data,:full_address)", row
            )
        for row in state.get("routes", []):
            self._conn.execute(
                "INSERT INTO routes VALUES (:id,:name,:notes,:display_order)", row
            )
        for row in state.get("route_visit_order", []):
            row.setdefault("office_color", "black")
            self._conn.execute(
                "INSERT INTO route_visit_order"
                "(id,route_id,visit_id,position,start_time,end_time,is_office_instance,office_name,office_address,office_color) VALUES "
                "(:id,:route_id,:visit_id,:position,:start_time,:end_time,"
                ":is_office_instance,:office_name,:office_address,:office_color)", row
            )
        for row in state.get("travel_segments", []):
            if "travel_time_state" not in row:
                row["travel_time_state"] = (
                    TravelTimeState.EDITED if bool(row.get("is_custom")) else (
                        TravelTimeState.CALCULATED if row.get("calculated_minutes") is not None
                        else TravelTimeState.DEFAULT
                    )
                )
            self._conn.execute(
                "INSERT INTO travel_segments VALUES "
                "(:id,:route_id,:from_entry_id,:to_entry_id,:mode,:travel_minutes,"
                ":is_custom,:calculated_minutes,:travel_time_state)", row
            )
        for row in state.get("empty_spaces", []):
            self._conn.execute(
                "INSERT INTO empty_spaces VALUES "
                "(:id,:route_id,:from_entry_id,:to_entry_id,:duration_minutes)", row
            )
        for row in state.get("extra_time_blocks", []):
            self._conn.execute(
                "INSERT INTO extra_time_blocks VALUES "
                "(:id,:route_id,:to_entry_id)", row
            )
        for row in state.get("settings", []):
            self._conn.execute(
                "INSERT OR REPLACE INTO settings VALUES (:key,:value)", row
            )
        for row in state.get("column_order", []):
            self._conn.execute(
                "INSERT OR REPLACE INTO column_order VALUES "
                "(:column_type,:column_id,:display_order)", row
            )
        for row in state.get("office_template", []):
            self._conn.execute(
                "INSERT OR REPLACE INTO office_template VALUES (:id,:name,:address)", row
            )
        self._conn.commit()
