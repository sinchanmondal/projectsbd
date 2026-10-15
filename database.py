"""
database.py
===========
Persistence layer for **Apex SBD – Powerlifting Progress & Analytics System**.

Responsibilities
----------------
* SQLite schema management (``sessions`` and ``settings`` tables, versioned
  through ``PRAGMA user_version``).
* Strict, user-friendly validation of raw form input (:func:`validate_session`).
* CRUD operations for training sessions (:class:`Database`).
* Lifter profile settings (display name and the sex used for DOTS).
* First-run sample data, CSV import/export and hot database backups.

The module has no GUI or analytics imports, so it can be reused from scripts
and unit tests on its own.
"""

from __future__ import annotations

import csv
import math
import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence, Union

PathLike = Union[str, Path]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH: Path = Path(__file__).resolve().parent / "apex_sbd.db"
SCHEMA_VERSION: int = 1

DATE_FORMAT: str = "%Y-%m-%d"
EARLIEST_SESSION_DATE: date = date(1950, 1, 1)
BODYWEIGHT_LIMITS_KG: tuple[float, float] = (30.0, 250.0)
LIFT_LIMIT_KG: float = 600.0
MAX_NOTES_LENGTH: int = 500
MAX_NAME_LENGTH: int = 40

LIFT_FIELDS: tuple[str, ...] = ("squat", "bench", "deadlift")
REQUIRED_FIELDS: tuple[str, ...] = ("session_date", "bodyweight", "squat", "bench", "deadlift")
VALID_SEXES: tuple[str, ...] = ("male", "female")

FIELD_LABELS: dict[str, str] = {
    "session_date": "Date",
    "bodyweight": "Bodyweight",
    "squat": "Squat",
    "bench": "Bench press",
    "deadlift": "Deadlift",
    "notes": "Notes",
}

SETTING_LIFTER_NAME: str = "lifter_name"
SETTING_LIFTER_SEX: str = "lifter_sex"
SETTING_INITIALIZED: str = "initialized"
DEFAULT_LIFTER_NAME: str = "Lifter"

CSV_EXPORT_HEADERS: tuple[str, ...] = (
    "Date", "Bodyweight (kg)", "Squat (kg)", "Bench (kg)", "Deadlift (kg)", "Total (kg)", "Notes",
)

# Normalised CSV header (lower-case, alphanumerics only) -> canonical field name.
_CSV_ALIASES: dict[str, str] = {
    "date": "session_date", "sessiondate": "session_date", "day": "session_date",
    "bodyweight": "bodyweight", "bodyweightkg": "bodyweight", "bw": "bodyweight", "bwkg": "bodyweight",
    "squat": "squat", "squatkg": "squat", "sq": "squat",
    "bench": "bench", "benchkg": "bench", "benchpress": "bench", "benchpresskg": "bench", "bp": "bench",
    "deadlift": "deadlift", "deadliftkg": "deadlift", "dl": "deadlift",
    "notes": "notes", "note": "notes", "comments": "notes", "comment": "notes",
}

_SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date  TEXT    NOT NULL
                  CHECK (session_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    bodyweight    REAL    NOT NULL CHECK (bodyweight > 0),
    squat         REAL    NOT NULL CHECK (squat > 0),
    bench         REAL    NOT NULL CHECK (bench > 0),
    deadlift      REAL    NOT NULL CHECK (deadlift > 0),
    notes         TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions (session_date);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

# (days before today, bodyweight, squat, bench, deadlift, notes) – a 12-week block.
SAMPLE_SESSION_PLAN: tuple[tuple[int, float, float, float, float, str], ...] = (
    (90, 82.4, 170.0, 115.0, 210.0, "Start of a 12-week strength block. Comp-style singles at RPE 9."),
    (73, 82.8, 175.0, 117.5, 215.0, "Squat depth felt consistent. Paused bench is getting crisper."),
    (56, 83.1, 177.5, 120.0, 220.0, "Bench finally hit 120. Slight hip tightness on the pull."),
    (38, 83.6, 182.5, 120.0, 225.0, "Bench stalled, but squat and deadlift keep climbing."),
    (20, 83.4, 185.0, 122.5, 230.0, "Fresh after the deload week. Everything moved fast."),
    (3, 83.9, 190.0, 125.0, 235.0, "Mock meet day: 550 kg total with PRs on all three lifts."),
)


# ---------------------------------------------------------------------------
# Errors and data containers
# ---------------------------------------------------------------------------
class DatabaseError(Exception):
    """Raised when a storage operation fails (I/O, corruption, missing rows…)."""


class ValidationError(ValueError):
    """Raised when user-supplied session data fails validation.

    Attributes:
        errors: Mapping of field name -> human-readable error message.
    """

    def __init__(self, errors: Mapping[str, str]) -> None:
        """Store per-field ``errors`` and build a readable summary message."""
        self.errors: dict[str, str] = dict(errors)
        super().__init__(self.summary())

    def summary(self) -> str:
        """Return every error message as a bullet list, one per line."""
        return "\n".join(f"• {message}" for message in self.errors.values())


@dataclass(frozen=True)
class SessionInput:
    """Validated session values ready to be written to the database."""

    session_date: date
    bodyweight: float
    squat: float
    bench: float
    deadlift: float
    notes: str = ""

    @property
    def total(self) -> float:
        """SBD total in kilograms."""
        return round(self.squat + self.bench + self.deadlift, 2)


@dataclass(frozen=True)
class SessionRecord:
    """A stored training session (one row of the ``sessions`` table)."""

    id: int
    session_date: date
    bodyweight: float
    squat: float
    bench: float
    deadlift: float
    notes: str = ""

    @property
    def total(self) -> float:
        """SBD total in kilograms."""
        return round(self.squat + self.bench + self.deadlift, 2)

    def to_input(self) -> SessionInput:
        """Return the record's values as a :class:`SessionInput` (without the id)."""
        return SessionInput(self.session_date, self.bodyweight, self.squat, self.bench, self.deadlift, self.notes)


@dataclass
class ImportReport:
    """Outcome of a CSV import."""

    imported: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> int:
        """Number of rows that failed validation."""
        return len(self.errors)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
_THOUSANDS_PATTERN = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_UNIT_SUFFIX_PATTERN = re.compile(r"\s*(kgs?|kilograms?)\s*$", re.IGNORECASE)


def _to_float(raw: Any, label: str) -> float:
    """Convert raw input (``str``/``int``/``float``) into a finite float.

    Accepts a trailing ``kg`` unit and a decimal comma (``82,5``).
    Raises ``ValueError`` with a user-facing message on failure.
    """
    if isinstance(raw, bool):
        raise ValueError(f"{label} must be a number.")
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = "" if raw is None else str(raw).strip()
        if not text:
            raise ValueError(f"{label} is required.")
        text = _UNIT_SUFFIX_PATTERN.sub("", text)
        text = text.replace(",", "") if _THOUSANDS_PATTERN.match(text) else text.replace(",", ".")
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{label} must be a number, '{raw}' is not valid.") from None
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number.")
    return value


def parse_bodyweight(raw: Any) -> float:
    """Validate a bodyweight in kg (must lie within :data:`BODYWEIGHT_LIMITS_KG`)."""
    label = FIELD_LABELS["bodyweight"]
    value = _to_float(raw, label)
    if value <= 0:
        raise ValueError(f"{label} must be a positive number (got {value:g} kg).")
    low, high = BODYWEIGHT_LIMITS_KG
    if not low <= value <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g} kg (got {value:g} kg).")
    return round(value, 2)


def parse_lift(raw: Any, label: str = "Lift") -> float:
    """Validate a lift weight in kg: positive and at most :data:`LIFT_LIMIT_KG`."""
    value = _to_float(raw, label)
    if value < 0:
        raise ValueError(f"{label} cannot be negative (got {value:g} kg).")
    if round(value, 2) <= 0:
        raise ValueError(f"{label} must be greater than 0 kg.")
    if value > LIFT_LIMIT_KG:
        raise ValueError(f"{label} of {value:g} kg is above the {LIFT_LIMIT_KG:g} kg limit.")
    return round(value, 2)


def parse_session_date(raw: Any) -> date:
    """Validate a session date (``YYYY-MM-DD`` string or ``date``), not in the future."""
    if isinstance(raw, datetime):
        value = raw.date()
    elif isinstance(raw, date):
        value = raw
    else:
        text = "" if raw is None else str(raw).strip()
        if not text:
            raise ValueError("Date is required.")
        try:
            value = datetime.strptime(text, DATE_FORMAT).date()
        except ValueError:
            raise ValueError(f"Date '{text}' is not valid. Use YYYY-MM-DD, e.g. {date.today():%Y-%m-%d}.") from None
    if value > date.today():
        raise ValueError("Date cannot be in the future.")
    if value < EARLIEST_SESSION_DATE:
        raise ValueError(f"Date must be on or after {EARLIEST_SESSION_DATE:%Y-%m-%d}.")
    return value


def clean_notes(raw: Any) -> str:
    """Normalise free-text notes and enforce :data:`MAX_NOTES_LENGTH`."""
    text = "" if raw is None else str(raw).replace("\r\n", "\n").strip()
    if len(text) > MAX_NOTES_LENGTH:
        raise ValueError(f"Notes are limited to {MAX_NOTES_LENGTH} characters (currently {len(text)}).")
    return text


def validate_sex(raw: Any) -> str:
    """Validate the lifter sex used for DOTS (``male`` or ``female``)."""
    value = str(raw or "").strip().lower()
    if value not in VALID_SEXES:
        raise ValueError("Sex must be either 'male' or 'female'.")
    return value


def validate_lifter_name(raw: Any) -> str:
    """Validate a display name (1–40 characters after trimming)."""
    value = " ".join(str(raw or "").split())
    if not value:
        raise ValueError("Name cannot be empty.")
    if len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"Name is limited to {MAX_NAME_LENGTH} characters.")
    return value


def validate_session(
    session_date: Any,
    bodyweight: Any,
    squat: Any,
    bench: Any,
    deadlift: Any,
    notes: Any = "",
) -> SessionInput:
    """Validate raw session input and return a :class:`SessionInput`.

    Every field is checked so the user sees all problems at once.

    Raises:
        ValidationError: with a ``field -> message`` mapping for each invalid field.
    """
    checks: dict[str, Callable[[], Any]] = {
        "session_date": lambda: parse_session_date(session_date),
        "bodyweight": lambda: parse_bodyweight(bodyweight),
        "squat": lambda: parse_lift(squat, FIELD_LABELS["squat"]),
        "bench": lambda: parse_lift(bench, FIELD_LABELS["bench"]),
        "deadlift": lambda: parse_lift(deadlift, FIELD_LABELS["deadlift"]),
        "notes": lambda: clean_notes(notes),
    }
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, check in checks.items():
        try:
            values[name] = check()
        except ValueError as exc:
            errors[name] = str(exc)
    if errors:
        raise ValidationError(errors)
    return SessionInput(**values)


def generate_sample_sessions(reference: Optional[date] = None) -> list[SessionInput]:
    """Build six realistic sessions spread over ~3 months that end shortly before ``reference``."""
    end = reference or date.today()
    return [
        validate_session(end - timedelta(days=days_ago), bw, squat, bench, deadlift, notes)
        for days_ago, bw, squat, bench, deadlift, notes in SAMPLE_SESSION_PLAN
    ]


def export_rows_to_csv(path: PathLike, headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
    """Write ``headers`` and ``rows`` to a UTF-8 CSV file and return the row count."""
    target = Path(path).expanduser()
    count = 0
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)
                count += 1
    except OSError as exc:
        raise DatabaseError(f"Could not write CSV file '{target}': {exc.strerror or exc}") from exc
    return count


def _fingerprint(data: SessionInput) -> tuple[str, float, float, float, float]:
    """Return a hashable identity used to detect duplicate sessions."""
    return (data.session_date.isoformat(), round(data.bodyweight, 2), round(data.squat, 2),
            round(data.bench, 2), round(data.deadlift, 2))


# ---------------------------------------------------------------------------
# Database access object
# ---------------------------------------------------------------------------
class Database:
    """Thin, safe wrapper around a single SQLite connection.

    Every method raises :class:`DatabaseError` (never a raw ``sqlite3`` error)
    so the UI can show a friendly message.
    """

    def __init__(self, path: PathLike = DEFAULT_DB_PATH) -> None:
        """Open (or create) the database at ``path`` and ensure the schema exists."""
        self.path: Union[str, Path] = ":memory:" if str(path) == ":memory:" else Path(path).expanduser().resolve()
        try:
            if isinstance(self.path, Path):
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path))
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseError(f"Could not open database '{self.path}': {exc}") from exc
        self._conn.row_factory = sqlite3.Row
        self.initialize_schema()

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self) -> "Database":
        """Support ``with Database(...) as db:`` usage."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the connection when leaving a ``with`` block."""
        self.close()

    def close(self) -> None:
        """Close the underlying connection (safe to call more than once)."""
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def initialize_schema(self) -> None:
        """Create tables and indexes if needed and stamp the schema version."""
        try:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"'{self.path}' is not a usable Apex SBD database: {exc}") from exc

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield the connection inside a transaction; commit on success, roll back on error."""
        try:
            with self._conn:
                yield self._conn
        except sqlite3.Error as exc:
            raise DatabaseError(f"Database write failed: {exc}") from exc

    def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Run a read-only query and return all rows."""
        try:
            return self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Database read failed: {exc}") from exc

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SessionRecord:
        """Convert a ``sessions`` row into a :class:`SessionRecord`."""
        return SessionRecord(
            id=int(row["id"]),
            session_date=date.fromisoformat(row["session_date"]),
            bodyweight=float(row["bodyweight"]),
            squat=float(row["squat"]),
            bench=float(row["bench"]),
            deadlift=float(row["deadlift"]),
            notes=row["notes"] or "",
        )

    @staticmethod
    def _params(data: SessionInput) -> tuple[Any, ...]:
        """Return the positional SQL parameters for a :class:`SessionInput`."""
        return (data.session_date.isoformat(), data.bodyweight, data.squat, data.bench, data.deadlift, data.notes)

    # -- CRUD ----------------------------------------------------------------
    def add_session(self, data: SessionInput) -> int:
        """Insert a validated session and return its new id."""
        with self._transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (session_date, bodyweight, squat, bench, deadlift, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                self._params(data),
            )
            return int(cursor.lastrowid or 0)

    def add_sessions(self, items: Iterable[SessionInput]) -> int:
        """Insert many validated sessions in one transaction and return how many were added."""
        params = [self._params(item) for item in items]
        if not params:
            return 0
        with self._transaction() as conn:
            conn.executemany(
                "INSERT INTO sessions (session_date, bodyweight, squat, bench, deadlift, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                params,
            )
        return len(params)

    def get_session(self, session_id: int) -> Optional[SessionRecord]:
        """Return the session with ``session_id`` or ``None`` if it does not exist."""
        rows = self._fetchall("SELECT * FROM sessions WHERE id = ?", (int(session_id),))
        return self._row_to_record(rows[0]) if rows else None

    def list_sessions(self, *, newest_first: bool = False) -> list[SessionRecord]:
        """Return every session ordered by date (oldest first unless ``newest_first``)."""
        order = "DESC" if newest_first else "ASC"
        rows = self._fetchall(f"SELECT * FROM sessions ORDER BY session_date {order}, id {order}")
        return [self._row_to_record(row) for row in rows]

    def update_session(self, session_id: int, data: SessionInput) -> None:
        """Overwrite an existing session with new validated values."""
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET session_date = ?, bodyweight = ?, squat = ?, bench = ?, deadlift = ?, "
                "notes = ?, updated_at = datetime('now') WHERE id = ?",
                (*self._params(data), int(session_id)),
            )
            if cursor.rowcount == 0:
                raise DatabaseError(f"Session #{session_id} no longer exists.")

    def delete_session(self, session_id: int) -> None:
        """Delete one session by id."""
        if self.delete_sessions([session_id]) == 0:
            raise DatabaseError(f"Session #{session_id} no longer exists.")

    def delete_sessions(self, session_ids: Sequence[int]) -> int:
        """Delete several sessions and return how many rows were removed."""
        ids = [int(i) for i in session_ids]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        with self._transaction() as conn:
            cursor = conn.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", ids)
            return cursor.rowcount

    def delete_all_sessions(self) -> int:
        """Delete every session, reset the id counter and return the number removed."""
        with self._transaction() as conn:
            removed = conn.execute("DELETE FROM sessions").rowcount
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'sessions'")
        return removed

    def count_sessions(self) -> int:
        """Return the number of stored sessions."""
        return int(self._fetchall("SELECT COUNT(*) AS n FROM sessions")[0]["n"])

    def is_duplicate(self, data: SessionInput, exclude_id: Optional[int] = None) -> bool:
        """Return ``True`` if an identical session (date, BW and all lifts) already exists."""
        rows = self._fetchall(
            "SELECT id FROM sessions WHERE session_date = ? AND ABS(bodyweight - ?) < 0.005 "
            "AND ABS(squat - ?) < 0.005 AND ABS(bench - ?) < 0.005 AND ABS(deadlift - ?) < 0.005",
            self._params(data)[:5],
        )
        return any(int(row["id"]) != exclude_id for row in rows)

    # -- settings ------------------------------------------------------------
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Return a stored setting value, or ``default`` when it is missing."""
        rows = self._fetchall("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        """Insert or update a setting."""
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def get_lifter_sex(self) -> str:
        """Return the lifter sex used for DOTS (falls back to ``male``)."""
        value = (self.get_setting(SETTING_LIFTER_SEX, "male") or "male").lower()
        return value if value in VALID_SEXES else "male"

    def set_lifter_sex(self, sex: str) -> None:
        """Validate and store the lifter sex."""
        self.set_setting(SETTING_LIFTER_SEX, validate_sex(sex))

    def get_lifter_name(self) -> str:
        """Return the lifter display name."""
        return self.get_setting(SETTING_LIFTER_NAME, DEFAULT_LIFTER_NAME) or DEFAULT_LIFTER_NAME

    def set_lifter_name(self, name: str) -> None:
        """Validate and store the lifter display name."""
        self.set_setting(SETTING_LIFTER_NAME, validate_lifter_name(name))

    # -- sample data ---------------------------------------------------------
    def seed_sample_data(self, *, force: bool = False) -> int:
        """Insert the sample training block if the table is empty (or ``force``)."""
        if not force and self.count_sessions() > 0:
            return 0
        return self.add_sessions(generate_sample_sessions())

    def ensure_initialized(self) -> bool:
        """First-launch hook: seed sample data exactly once. Returns ``True`` if seeded."""
        if self.get_setting(SETTING_INITIALIZED) is not None:
            return False
        seeded = self.seed_sample_data() > 0
        if self.get_setting(SETTING_LIFTER_NAME) is None:
            self.set_setting(SETTING_LIFTER_NAME, DEFAULT_LIFTER_NAME)
        if self.get_setting(SETTING_LIFTER_SEX) is None:
            self.set_setting(SETTING_LIFTER_SEX, "male")
        self.set_setting(SETTING_INITIALIZED, "1")
        return seeded

    def reset_to_sample_data(self) -> int:
        """Atomically replace every session with the sample training block."""
        samples = generate_sample_sessions()
        with self._transaction() as conn:
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'sessions'")
            conn.executemany(
                "INSERT INTO sessions (session_date, bodyweight, squat, bench, deadlift, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [self._params(item) for item in samples],
            )
        return len(samples)

    # -- import / export / backup -------------------------------------------
    def export_sessions_to_csv(self, path: PathLike) -> int:
        """Export every session (oldest first) to CSV and return the row count."""
        rows = (
            (s.session_date.isoformat(), s.bodyweight, s.squat, s.bench, s.deadlift, s.total, s.notes)
            for s in self.list_sessions()
        )
        return export_rows_to_csv(path, CSV_EXPORT_HEADERS, rows)

    def import_sessions_from_csv(self, path: PathLike) -> ImportReport:
        """Import sessions from a CSV file.

        Header names are matched loosely (e.g. ``Date``, ``Bodyweight (kg)``,
        ``Bench Press``). Every row is validated; invalid rows are reported and
        exact duplicates of existing sessions are skipped.
        """
        source = Path(path).expanduser()
        try:
            with source.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                headers = reader.fieldnames or []
                if not headers:
                    raise DatabaseError("The CSV file is empty.")
                mapping: dict[str, str] = {}
                for header in headers:
                    canonical = _CSV_ALIASES.get(re.sub(r"[^a-z0-9]", "", (header or "").lower()))
                    if canonical and canonical not in mapping.values():
                        mapping[header] = canonical
                missing = [FIELD_LABELS[name] for name in REQUIRED_FIELDS if name not in mapping.values()]
                if missing:
                    raise DatabaseError("The CSV is missing required column(s): " + ", ".join(missing) + ".")
                rows = list(reader)
        except OSError as exc:
            raise DatabaseError(f"Could not read '{source}': {exc.strerror or exc}") from exc
        except (UnicodeDecodeError, csv.Error) as exc:
            raise DatabaseError(f"'{source.name}' is not a readable UTF-8 CSV file: {exc}") from exc

        report = ImportReport()
        seen = {_fingerprint(record.to_input()) for record in self.list_sessions()}
        accepted: list[SessionInput] = []
        for line_number, row in enumerate(rows, start=2):
            if all(not str(value).strip() for value in row.values() if isinstance(value, str)):
                continue  # blank line
            values = {canonical: row.get(header) or "" for header, canonical in mapping.items()}
            try:
                data = validate_session(
                    values["session_date"], values["bodyweight"], values["squat"],
                    values["bench"], values["deadlift"], values.get("notes", ""),
                )
            except ValidationError as exc:
                report.errors.append(f"Row {line_number}: " + " ".join(exc.errors.values()))
                continue
            fingerprint = _fingerprint(data)
            if fingerprint in seen:
                report.duplicates += 1
                continue
            seen.add(fingerprint)
            accepted.append(data)
        report.imported = self.add_sessions(accepted)
        return report

    def backup_to(self, destination: PathLike) -> Path:
        """Write a consistent copy of the live database to ``destination``."""
        target = Path(destination).expanduser().resolve()
        if isinstance(self.path, Path) and target == self.path:
            raise DatabaseError("Choose a different file than the live database for the backup.")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(str(target))) as backup_conn:
                self._conn.backup(backup_conn)
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseError(f"Backup to '{target}' failed: {exc}") from exc
        return target


if __name__ == "__main__":
    # Smoke test against an in-memory database.
    with Database(":memory:") as database:
        database.ensure_initialized()
        for record in database.list_sessions():
            print(f"#{record.id} {record.session_date}  BW {record.bodyweight:>5}  "
                  f"S {record.squat:>5}  B {record.bench:>5}  D {record.deadlift:>5}  = {record.total} kg")
        try:
            validate_session("2026-13-40", "-80", "abc", "0", "nan")
        except ValidationError as error:
            print("Validation demo:\n" + error.summary())
