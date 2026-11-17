"""Append-only SQLite journal for stage-4 text/event records.

This module is intentionally separate from the injected native bridge.  It
persists records that a reader has already received from
``native/stage4_event_transport.h``; the caller is responsible for calling
the native acknowledgment only after this module's transaction commits.

Design boundaries:

* one immutable run row per ``start_run(epoch)`` call; reusing an epoch
  creates a new run and therefore cannot collide with an older run;
* ``(run_id, sequence)`` is unique, and sequence equality is used only for
  replay idempotency (same sequence + same payload is a duplicate; same
  sequence + different payload is a conflict);
* event rows are never updated or deleted (SQLite triggers reject both);
* text is stored as a BLOB with an explicit byte count, so repeats,
  newlines, NUL bytes, and non-UTF-8 bytes survive verbatim;
* no whole-session read API exists.  ``read_events`` requires an explicit
  positive ``limit`` and uses a keyset cursor;
* SQLite errors are not swallowed.  The acknowledgment callback is invoked
  only after a successful commit, so a write/commit error cannot be
  acknowledged as persisted.

This module does not import the game, the native bridge, or any network
service.  ``received_monotonic_ns`` is taken from an injectable clock for
tests; it is deliberately distinct from the event's observed game tick.
"""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

EVENT_TEXT_BYTES = 218
SCHEMA_VERSION = 1
UINT32_MAX = 0xFFFFFFFF
INT32_MIN = -0x80000000
INT32_MAX = 0x7FFFFFFF
INT64_MAX = 0x7FFFFFFFFFFFFFFF

__all__ = [
    "AppendResult",
    "EVENT_TEXT_BYTES",
    "Event",
    "Journal",
    "JournalClosedError",
    "JournalConflict",
    "JournalCursorError",
    "JournalError",
    "JournalEvent",
    "JournalOrderError",
    "JournalSchemaError",
    "JournalValidationError",
    "Page",
    "RunStatus",
]


class JournalError(Exception):
    """Base class for journal-specific failures."""


class JournalValidationError(JournalError, ValueError):
    """A caller supplied a non-record value or an invalid field."""


class JournalConflict(JournalError):
    """A sequence was replayed with a different immutable payload."""


class JournalOrderError(JournalError):
    """A new sequence arrived behind the run's persisted head."""


class JournalClosedError(JournalError):
    """The run has been closed and must not receive more events."""


class JournalSchemaError(JournalError):
    """The database schema/version is not the supported layout."""


class JournalCursorError(JournalValidationError):
    """The pagination cursor is malformed or belongs to another run."""


@dataclass(frozen=True)
class Event:
    """One event to append.  ``text_bytes`` is optional only when it equals
    ``len(text)``; an explicit mismatch is rejected rather than repaired."""

    sequence: int
    epoch: int
    observed_tick: int
    type: int
    x: int
    y: int
    color: int
    text: bytes
    text_bytes: int | None = None


@dataclass(frozen=True)
class JournalEvent:
    """One persisted event row, including receive and gap metadata."""

    run_id: int
    sequence: int
    epoch: int
    observed_tick: int
    received_monotonic_ns: int
    type: int
    x: int
    y: int
    color: int
    text_bytes: int
    text: bytes
    overflow: bool
    gap: bool


@dataclass(frozen=True)
class RunStatus:
    run_id: int
    epoch: int
    started_ns: int
    closed_ns: int | None
    overflow: bool
    lost_max: int
    last_sequence: int
    event_count: int
    gap_events: int
    missing_sequences: int
    first_received_ns: int | None
    last_received_ns: int | None
    last_observed_tick: int | None


@dataclass(frozen=True)
class AppendResult:
    run_id: int
    inserted: int
    duplicates: int
    persisted_through: int | None
    acknowledged: bool
    ack_next: int | None
    overflow: bool
    gap_events: int
    missing_sequences: int


@dataclass(frozen=True)
class Page:
    events: tuple[JournalEvent, ...]
    next_cursor: str | None
    has_more: bool


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch INTEGER NOT NULL CHECK (epoch BETWEEN 0 AND 4294967295),
    started_ns INTEGER NOT NULL CHECK (started_ns >= 0),
    closed_ns INTEGER,
    overflow INTEGER NOT NULL DEFAULT 0 CHECK (overflow IN (0, 1)),
    lost_max INTEGER NOT NULL DEFAULT 0 CHECK (lost_max >= 0),
    last_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    event_count INTEGER NOT NULL DEFAULT 0 CHECK (event_count >= 0),
    gap_events INTEGER NOT NULL DEFAULT 0 CHECK (gap_events >= 0),
    missing_sequences INTEGER NOT NULL DEFAULT 0
        CHECK (missing_sequences >= 0),
    first_received_ns INTEGER,
    last_received_ns INTEGER,
    last_observed_tick INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    run_id INTEGER NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 4294967295),
    epoch INTEGER NOT NULL CHECK (epoch BETWEEN 0 AND 4294967295),
    observed_tick INTEGER NOT NULL
        CHECK (observed_tick BETWEEN 0 AND 4294967295),
    received_monotonic_ns INTEGER NOT NULL
        CHECK (received_monotonic_ns >= 0),
    event_type INTEGER NOT NULL
        CHECK (event_type BETWEEN 0 AND 4294967295),
    x INTEGER NOT NULL CHECK (x BETWEEN -2147483648 AND 2147483647),
    y INTEGER NOT NULL CHECK (y BETWEEN -2147483648 AND 2147483647),
    color INTEGER NOT NULL CHECK (color BETWEEN 0 AND 4294967295),
    text_bytes INTEGER NOT NULL CHECK (text_bytes BETWEEN 0 AND 218),
    text BLOB NOT NULL,
    overflow INTEGER NOT NULL CHECK (overflow IN (0, 1)),
    gap INTEGER NOT NULL CHECK (gap IN (0, 1)),
    PRIMARY KEY (run_id, sequence),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE RESTRICT,
    CHECK (length(text) = text_bytes)
) WITHOUT ROWID;

CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'event journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'event journal is append-only');
END;
"""


def _require_int(name: str, value: object, low: int, high: int) -> int:
    if type(value) is not int:
        raise JournalValidationError(
            f"{name} must be an int, not {type(value).__name__}"
        )
    if value < low or value > high:
        raise JournalValidationError(
            f"{name} must be between {low} and {high}, got {value}"
        )
    return value


def _uint32(name: str, value: object) -> int:
    return _require_int(name, value, 0, UINT32_MAX)


def _int32(name: str, value: object) -> int:
    return _require_int(name, value, INT32_MIN, INT32_MAX)


def _nonnegative_int(name: str, value: object) -> int:
    return _require_int(name, value, 0, INT64_MAX)


def _as_bytes(name: str, value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise JournalValidationError(
        f"{name} must be bytes-like, not {type(value).__name__}; "
        "text is never encoded or normalized"
    )


def _normalize_event(event: Event) -> tuple[int, tuple[object, ...]]:
    if not isinstance(event, Event):
        raise JournalValidationError(
            f"events must be Event instances, not {type(event).__name__}"
        )
    sequence = _require_int("sequence", event.sequence, 1, UINT32_MAX)
    epoch = _uint32("epoch", event.epoch)
    observed_tick = _uint32("observed_tick", event.observed_tick)
    event_type = _uint32("type", event.type)
    x = _int32("x", event.x)
    y = _int32("y", event.y)
    color = _uint32("color", event.color)
    text = _as_bytes("text", event.text)
    if len(text) > EVENT_TEXT_BYTES:
        raise JournalValidationError(
            f"text is {len(text)} bytes; maximum is {EVENT_TEXT_BYTES}"
        )
    if event.text_bytes is None:
        text_bytes = len(text)
    else:
        text_bytes = _require_int(
            "text_bytes", event.text_bytes, 0, EVENT_TEXT_BYTES
        )
        if text_bytes != len(text):
            raise JournalValidationError(
                "text_bytes must equal len(text); no implicit padding or "
                "truncation is performed"
            )
    payload = (epoch, observed_tick, event_type, x, y, color, text_bytes, text)
    return sequence, payload


def _payload_from_row(row: sqlite3.Row) -> tuple[object, ...]:
    return (
        int(row["epoch"]),
        int(row["observed_tick"]),
        int(row["event_type"]),
        int(row["x"]),
        int(row["y"]),
        int(row["color"]),
        int(row["text_bytes"]),
        bytes(row["text"]),
    )


def _row_to_event(run_id: int, row: sqlite3.Row) -> JournalEvent:
    return JournalEvent(
        run_id=run_id,
        sequence=int(row["sequence"]),
        epoch=int(row["epoch"]),
        observed_tick=int(row["observed_tick"]),
        received_monotonic_ns=int(row["received_monotonic_ns"]),
        type=int(row["event_type"]),
        x=int(row["x"]),
        y=int(row["y"]),
        color=int(row["color"]),
        text_bytes=int(row["text_bytes"]),
        text=bytes(row["text"]),
        overflow=bool(row["overflow"]),
        gap=bool(row["gap"]),
    )


def _encode_cursor(run_id: int, after_sequence: int) -> str:
    payload = json.dumps(
        {"v": 1, "run": run_id, "after": after_sequence},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: object, run_id: int) -> int:
    if not isinstance(cursor, str):
        raise JournalCursorError("cursor must be a string")
    try:
        raw = base64.b64decode(
            cursor.encode("ascii"), altchars=b"-_", validate=True
        )
        decoded = json.loads(raw.decode("ascii"))
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise JournalCursorError("cursor is not valid journal cursor data") from exc
    if not isinstance(decoded, dict):
        raise JournalCursorError("cursor payload must be an object")
    if type(decoded.get("v")) is not int or decoded["v"] != 1:
        raise JournalCursorError("unsupported cursor version")
    if type(decoded.get("run")) is not int or decoded["run"] != run_id:
        raise JournalCursorError("cursor belongs to a different run")
    after = decoded.get("after")
    if type(after) is not int or not 0 <= after <= UINT32_MAX:
        raise JournalCursorError("cursor position is invalid")
    return after


class Journal:
    """A single-threaded SQLite journal handle.

    The caller supplies a path and, optionally, a monotonic clock returning
    integer nanoseconds.  The handle is not internally synchronized; use one
    handle from one thread, matching one external reader/acknowledger.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if not callable(clock_ns):
            raise JournalValidationError("clock_ns must be callable")
        self.path = Path(path)
        self._clock_ns = clock_ns
        connection = sqlite3.connect(str(self.path), isolation_level=None)
        self._conn: sqlite3.Connection | None = connection
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if version not in (0, SCHEMA_VERSION):
                raise JournalSchemaError(
                    f"unsupported journal schema version {version}"
                )
            connection.executescript(_SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except BaseException:
            connection.close()
            self._conn = None
            raise

    def close(self) -> None:
        conn = self._conn
        if conn is not None:
            self._conn = None
            conn.close()

    def __enter__(self) -> "Journal":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_open(self) -> sqlite3.Connection:
        conn = self._conn
        if conn is None:
            raise JournalClosedError("journal handle is closed")
        return conn

    def _now(self) -> int:
        return _nonnegative_int("clock_ns()", self._clock_ns())

    @staticmethod
    def _run_id(value: object) -> int:
        return _require_int("run_id", value, 1, INT64_MAX)

    def _run_row(
        self, run_id: int, conn: sqlite3.Connection | None = None
    ) -> sqlite3.Row:
        connection = conn if conn is not None else self._ensure_open()
        row = connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise JournalValidationError(f"unknown run_id {run_id}")
        return row

    def start_run(self, epoch: int, *, started_ns: int | None = None) -> int:
        """Create a new isolated run, even when ``epoch`` was used before."""

        connection = self._ensure_open()
        epoch = _uint32("epoch", epoch)
        started = self._now() if started_ns is None else _nonnegative_int(
            "started_ns", started_ns
        )
        cursor = connection.execute(
            "INSERT INTO runs (epoch, started_ns) VALUES (?, ?)",
            (epoch, started),
        )
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, closed_ns: int | None = None) -> int:
        """Mark a run closed.  Existing event rows are untouched."""

        connection = self._ensure_open()
        run_id = self._run_id(run_id)
        closed = self._now() if closed_ns is None else _nonnegative_int(
            "closed_ns", closed_ns
        )
        cursor = connection.execute(
            "UPDATE runs SET closed_ns = ? "
            "WHERE run_id = ? AND closed_ns IS NULL",
            (closed, run_id),
        )
        if cursor.rowcount == 0:
            row = self._run_row(run_id, connection)
            if row["closed_ns"] is not None:
                return int(row["closed_ns"])
            raise JournalValidationError(f"run_id {run_id} was not updated")
        return closed

    def append(
        self,
        run_id: int,
        events: Iterable[Event],
        *,
        overflow: bool = False,
        lost: int = 0,
        ack: Callable[[int], None] | None = None,
    ) -> AppendResult:
        """Append a batch in one transaction.

        ``overflow``/``lost`` are transport-status observations, not event
        payload.  They may be supplied with an empty ``events`` iterable to
        update the run's sticky overflow state.  ``lost`` is the highest
        observed native lost counter; it never decreases within a run.

        ``ack`` is invoked only after a successful COMMIT. New sequences
        must form a contiguous prefix; an out-of-order/gap batch is rolled
        back without acknowledging it, so the missing records can be read
        before retrying that batch. Legacy runs with gaps remain readable
        but cannot receive further appends; explicit recovery is required.
        """

        run_id = self._run_id(run_id)
        if type(overflow) is not bool:
            raise JournalValidationError("overflow must be a bool")
        lost = _nonnegative_int("lost", lost)
        if ack is not None and not callable(ack):
            raise JournalValidationError("ack must be callable or None")
        try:
            iterator = iter(events)
        except TypeError as exc:
            raise JournalValidationError("events must be an iterable") from exc

        connection = self._ensure_open()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._run_row(run_id, connection)
            if row["closed_ns"] is not None:
                raise JournalClosedError(f"run_id {run_id} is closed")
            run_epoch = int(row["epoch"])
            last_sequence = int(row["last_sequence"])
            event_count = int(row["event_count"])
            gap_events = int(row["gap_events"])
            missing_sequences = int(row["missing_sequences"])
            if gap_events or missing_sequences:
                raise JournalOrderError('legacy run contains a sequence gap; explicit recovery required')
            first_received_ns = row["first_received_ns"]
            last_received_ns = row["last_received_ns"]
            last_observed_tick = row["last_observed_tick"]
            overflow_now = bool(row["overflow"]) or overflow
            lost_max = max(int(row["lost_max"]), lost)

            inserted = 0
            duplicates = 0
            added_gap_events = 0
            added_missing_sequences = 0
            saw_event = False

            for event in iterator:
                sequence, payload = _normalize_event(event)
                saw_event = True
                if payload[0] != run_epoch:
                    raise JournalValidationError(
                        f"event epoch {payload[0]} does not match run epoch "
                        f"{run_epoch}"
                    )
                existing = connection.execute(
                    "SELECT epoch, observed_tick, event_type, x, y, color, "
                    "text_bytes, text FROM events "
                    "WHERE run_id = ? AND sequence = ?",
                    (run_id, sequence),
                ).fetchone()
                if existing is not None:
                    if _payload_from_row(existing) != payload:
                        raise JournalConflict(
                            f"sequence {sequence} already has a different "
                            "payload in this run"
                        )
                    duplicates += 1
                    continue
                if sequence <= last_sequence:
                    raise JournalOrderError(
                        f"sequence {sequence} is behind persisted head "
                        f"{last_sequence}"
                    )
                if sequence != last_sequence + 1:
                    raise JournalOrderError(
                        f'sequence {sequence} is not contiguous after {last_sequence}; batch rolled back'
                    )

                gap = sequence > last_sequence + 1
                missing_for_event = (
                    sequence - last_sequence - 1 if gap else 0
                )
                received_ns = self._now()
                connection.execute(
                    "INSERT INTO events ("
                    "run_id, sequence, epoch, observed_tick, "
                    "received_monotonic_ns, event_type, x, y, color, "
                    "text_bytes, text, overflow, gap"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        sequence,
                        payload[0],
                        payload[1],
                        received_ns,
                        payload[2],
                        payload[3],
                        payload[4],
                        payload[5],
                        payload[6],
                        sqlite3.Binary(payload[7]),
                        1 if overflow_now else 0,
                        1 if gap else 0,
                    ),
                )
                inserted += 1
                event_count += 1
                last_sequence = sequence
                if gap:
                    gap_events += 1
                    missing_sequences += missing_for_event
                    added_gap_events += 1
                    added_missing_sequences += missing_for_event
                if first_received_ns is None:
                    first_received_ns = received_ns
                last_received_ns = received_ns
                last_observed_tick = payload[1]

            connection.execute(
                "UPDATE runs SET overflow = ?, lost_max = ?, "
                "last_sequence = ?, event_count = ?, gap_events = ?, "
                "missing_sequences = ?, first_received_ns = ?, "
                "last_received_ns = ?, last_observed_tick = ? "
                "WHERE run_id = ?",
                (
                    1 if overflow_now else 0,
                    lost_max,
                    last_sequence,
                    event_count,
                    gap_events,
                    missing_sequences,
                    first_received_ns,
                    last_received_ns,
                    last_observed_tick,
                    run_id,
                ),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise

        ack_next = None
        acknowledged = False
        if (
            ack is not None
            and saw_event
            and gap_events == 0
            and last_sequence < UINT32_MAX
        ):
            ack_next = last_sequence + 1
            ack(ack_next)
            acknowledged = True
        return AppendResult(
            run_id=run_id,
            inserted=inserted,
            duplicates=duplicates,
            persisted_through=last_sequence if saw_event else None,
            acknowledged=acknowledged,
            ack_next=ack_next,
            overflow=overflow_now,
            gap_events=added_gap_events,
            missing_sequences=added_missing_sequences,
        )

    def status(self, run_id: int) -> RunStatus:
        connection = self._ensure_open()
        run_id = self._run_id(run_id)
        row = self._run_row(run_id, connection)
        return RunStatus(
            run_id=run_id,
            epoch=int(row["epoch"]),
            started_ns=int(row["started_ns"]),
            closed_ns=(
                None if row["closed_ns"] is None else int(row["closed_ns"])
            ),
            overflow=bool(row["overflow"]),
            lost_max=int(row["lost_max"]),
            last_sequence=int(row["last_sequence"]),
            event_count=int(row["event_count"]),
            gap_events=int(row["gap_events"]),
            missing_sequences=int(row["missing_sequences"]),
            first_received_ns=(
                None
                if row["first_received_ns"] is None
                else int(row["first_received_ns"])
            ),
            last_received_ns=(
                None
                if row["last_received_ns"] is None
                else int(row["last_received_ns"])
            ),
            last_observed_tick=(
                None
                if row["last_observed_tick"] is None
                else int(row["last_observed_tick"])
            ),
        )

    def read_events(
        self,
        run_id: int,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> Page:
        """Read at most ``limit`` events after ``cursor``.

        ``limit`` is mandatory and must be a positive int.  The query fetches
        at most ``limit + 1`` rows to determine ``has_more``; it never loads
        the whole run.
        """

        connection = self._ensure_open()
        run_id = self._run_id(run_id)
        if type(limit) is not int or limit <= 0:
            raise JournalValidationError(
                "limit must be a positive int; unbounded reads are not offered"
            )
        self._run_row(run_id, connection)
        after_sequence = 0
        if cursor is not None:
            after_sequence = _decode_cursor(cursor, run_id)

        rows = connection.execute(
            "SELECT run_id, sequence, epoch, observed_tick, "
            "received_monotonic_ns, event_type, x, y, color, text_bytes, "
            "text, overflow, gap FROM events "
            "WHERE run_id = ? AND sequence > ? "
            "ORDER BY sequence ASC LIMIT ?",
            (run_id, after_sequence, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        events = tuple(_row_to_event(run_id, row) for row in rows)
        next_cursor = (
            _encode_cursor(run_id, events[-1].sequence)
            if events
            else cursor
        )
        return Page(events=events, next_cursor=next_cursor, has_more=has_more)
