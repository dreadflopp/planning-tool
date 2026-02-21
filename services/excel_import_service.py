"""Excel import service – parses .xls and .xlsx exports and builds Visit objects."""

import json
import re
from datetime import datetime
from typing import Optional

import openpyxl

from domain.models import Visit, VisitColor
from services.persistence_service import PersistenceService


# Swedish color keywords (case-insensitive)
_COLOR_MAP = {
    "grön": VisitColor.GREEN,
    "rosa": VisitColor.PINK,
    "blå": VisitColor.BLUE,
}


def _strip_postal_and_city(address: str) -> str:
    """Remove 5-digit Swedish postal code (e.g. '12345' or '123 45') and city."""
    return re.sub(r"\s*\d{3}\s?\d{2}\s+\S.*$", "", address).strip()


def _extract_street(address: str) -> str:
    """Everything before the first digit."""
    m = re.search(r"\d", address)
    if m:
        return address[: m.start()].strip().rstrip(",").strip()
    return address.strip()


def _parse_time(value) -> str:
    """Convert Excel datetime or string to HH:MM."""
    if value is None:
        return "00:00"
    if isinstance(value, datetime):
        return f"{value.hour:02d}:{value.minute:02d}"
    if hasattr(value, "hour"):
        return f"{value.hour:02d}:{value.minute:02d}"
    s = str(value).strip()
    # Expect 'YYYY-MM-DD HH:MM:SS' or 'HH:MM'
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    return "00:00"


def _read_xls(filepath: str) -> tuple[list[str], list[list]]:
    """Read a legacy .xls file using xlrd. Returns (headers, rows_of_values)."""
    import xlrd
    book = xlrd.open_workbook(filepath)
    sheet = book.sheet_by_index(0)
    if sheet.nrows == 0:
        return [], []
    headers = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
    rows = []
    for row_idx in range(1, sheet.nrows):
        row_vals = []
        for c in range(sheet.ncols):
            cell = sheet.cell(row_idx, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                dt_tuple = xlrd.xldate_as_tuple(cell.value, book.datemode)
                row_vals.append(datetime(*dt_tuple))
            else:
                row_vals.append(cell.value)
        rows.append(row_vals)
    return headers, rows


def _read_xlsx(filepath: str) -> tuple[list[str], list[list]]:
    """Read a .xlsx file using openpyxl. Returns (headers, rows_of_values)."""
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not all_rows:
        return [], []
    headers = [str(h).strip() if h is not None else "" for h in all_rows[0]]
    rows = [list(r) for r in all_rows[1:]]
    return headers, rows


def _extract_color(slinga: str) -> Optional[str]:
    if not slinga:
        return None
    low = slinga.lower()
    for keyword, color in _COLOR_MAP.items():
        if keyword in low:
            return color
    return None


class ImportResult:
    def __init__(self):
        self.total_rows: int = 0
        self.imported: int = 0
        self.skipped: int = 0
        self.duplicates_in_file: list[str] = []   # object_ids that appeared >1 in file
        self.removed_object_ids: list[str] = []   # only set when user confirmed removal
        self.warnings: list[str] = []


class ExcelImportService:
    def __init__(self, persistence: PersistenceService):
        self._db = persistence

    def preview_import(self, filepath: str) -> tuple[list[Visit], ImportResult]:
        """Parse the Excel file (.xls or .xlsx) and return (new_visit_list, result)."""
        ext = filepath.lower().rsplit(".", 1)[-1]
        if ext == "xls":
            headers, rows = _read_xls(filepath)
        else:
            headers, rows = _read_xlsx(filepath)

        if not headers:
            return [], ImportResult()

        result = ImportResult()
        result.total_rows = len(rows)

        def col(row_vals, name: str):
            try:
                idx = headers.index(name)
                v = row_vals[idx]
                return str(v).strip() if v is not None else ""
            except (ValueError, IndexError):
                return ""

        seen_object_ids: dict[str, int] = {}
        visits: list[Visit] = []

        for row_vals in rows:
            object_id = col(row_vals, "ObjectID")
            if not object_id:
                result.skipped += 1
                result.warnings.append("Row skipped: missing ObjectID")
                continue

            raw_address = col(row_vals, "Adress")
            if not raw_address:
                result.skipped += 1
                continue

            # Track in-file duplicates
            if object_id in seen_object_ids:
                result.duplicates_in_file.append(object_id)
                result.warnings.append(f"Duplicate ObjectID in file: {object_id}")
                result.skipped += 1
                continue
            seen_object_ids[object_id] = 1

            address = _strip_postal_and_city(raw_address)
            if not address:
                result.skipped += 1
                continue

            street = _extract_street(address)
            name = col(row_vals, "Namn")
            start_time = _parse_time(col(row_vals, "Starttid") or None)
            end_time = _parse_time(col(row_vals, "Sluttid") or None)
            insatser = col(row_vals, "Insatser")
            slinga = col(row_vals, "Slinga")
            color = _extract_color(slinga)

            raw_data = {}
            for h, v in zip(headers, row_vals):
                raw_data[h] = str(v) if v is not None else ""

            v = Visit(
                id=None,
                object_id=object_id,
                name=name,
                address=address,        # display: postal code + city stripped
                street=street,
                default_start=start_time,
                default_end=end_time,
                insatser=insatser,
                color=color,
                raw_data=json.dumps(raw_data, ensure_ascii=False),
                full_address=raw_address,  # original with postal code – used for travel API
            )
            visits.append(v)
            result.imported += 1

        return visits, result

    def commit_import(self, visits: list[Visit], filepath: str,
                      result: ImportResult) -> list[str]:
        """Write parsed visits to DB. Returns list of object_ids not in new import."""
        existing_ids = self._db.get_all_object_ids()
        new_ids = {v.object_id for v in visits}
        removed_ids = list(existing_ids - new_ids)

        for v in visits:
            self._db.upsert_visit(v)

        self._db.save_import_metadata(
            filepath, result.total_rows, result.imported,
            result.skipped, len(result.duplicates_in_file)
        )
        return removed_ids

    def remove_visits_not_in_import(self, removed_object_ids: list[str]) -> list[int]:
        """Delete visits by object_id. Returns list of route_entry IDs that were placed."""
        placed_visit_ids = self._db.get_placed_visit_ids()
        affected_entry_ids = []
        for oid in removed_object_ids:
            v = self._db.get_visit_by_object_id(oid)
            if v and v.id:
                if v.id in placed_visit_ids:
                    affected_entry_ids.append(v.id)
                self._db.delete_visit(v.id)
        return affected_entry_ids
