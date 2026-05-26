"""
database.py  –  SQLite schema + helpers for TeslaLogger

Tables
──────
state_snapshots   raw telemetry (one row per poll)
drives            detected drive sessions
charges           detected charge sessions
waypoints         GPS track points during drives
states            car state history (asleep / online / driving / charging)
software_updates  software version change log
"""

from __future__ import annotations

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS state_snapshots (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at              TEXT NOT NULL,
    vin                      TEXT,
    display_name             TEXT,
    shift_state              TEXT,
    speed                    REAL,
    latitude                 REAL,
    longitude                REAL,
    heading                  REAL,
    odometer                 REAL,
    power                    REAL,
    battery_level            REAL,
    battery_range            REAL,
    est_battery_range        REAL,
    charging_state           TEXT,
    charge_rate              REAL,
    charger_power            REAL,
    charge_energy_added      REAL,
    charge_miles_added_rated REAL,
    charge_limit_soc         REAL,
    time_to_full_charge      REAL,
    inside_temp              REAL,
    outside_temp             REAL,
    is_climate_on            INTEGER,
    locked                   INTEGER,
    software_version         TEXT
);

CREATE TABLE IF NOT EXISTS drives (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    vin              TEXT,
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    start_latitude   REAL,
    start_longitude  REAL,
    end_latitude     REAL,
    end_longitude    REAL,
    start_address    TEXT,
    end_address      TEXT,
    distance_miles   REAL,
    duration_min     REAL,
    max_speed        REAL,
    start_range      REAL,
    end_range        REAL,
    range_used       REAL,
    efficiency_wh_mi REAL
);

CREATE TABLE IF NOT EXISTS charges (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    vin                     TEXT,
    started_at              TEXT NOT NULL,
    ended_at                TEXT,
    latitude                REAL,
    longitude               REAL,
    address                 TEXT,
    start_battery_level     REAL,
    end_battery_level       REAL,
    energy_added_kwh        REAL,
    miles_added             REAL,
    charge_limit_soc        REAL,
    max_charger_power       REAL,
    duration_min            REAL
);

CREATE TABLE IF NOT EXISTS waypoints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id    INTEGER NOT NULL REFERENCES drives(id),
    recorded_at TEXT NOT NULL,
    latitude    REAL,
    longitude   REAL,
    speed       REAL,
    power       REAL,
    odometer    REAL
);

CREATE TABLE IF NOT EXISTS states (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    vin        TEXT,
    state      TEXT NOT NULL,   -- asleep | online | driving | charging
    started_at TEXT NOT NULL,
    ended_at   TEXT
);

CREATE TABLE IF NOT EXISTS software_updates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vin         TEXT,
    version     TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_recorded ON state_snapshots(recorded_at);
CREATE INDEX IF NOT EXISTS idx_waypoints_drive    ON waypoints(drive_id);
CREATE INDEX IF NOT EXISTS idx_states_vin         ON states(vin, started_at);
"""

# ─────────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────────

def init_db(db_path: str | Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_DDL)
    log.info("Database ready at %s", db_path)


@contextmanager
def get_conn(db_path: str | Path) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

# ─────────────────────────────────────────────────────────────────────────────
# Snapshots
# ─────────────────────────────────────────────────────────────────────────────

def insert_snapshot(db_path: str | Path, state: dict[str, Any]) -> int:
    sql = """
        INSERT INTO state_snapshots (
            recorded_at, vin, display_name, shift_state, speed,
            latitude, longitude, heading, odometer, power,
            battery_level, battery_range, est_battery_range,
            charging_state, charge_rate, charger_power,
            charge_energy_added, charge_miles_added_rated, charge_limit_soc,
            time_to_full_charge, inside_temp, outside_temp,
            is_climate_on, locked, software_version
        ) VALUES (
            :recorded_at, :vin, :display_name, :shift_state, :speed,
            :latitude, :longitude, :heading, :odometer, :power,
            :battery_level, :battery_range, :est_battery_range,
            :charging_state, :charge_rate, :charger_power,
            :charge_energy_added, :charge_miles_added_rated, :charge_limit_soc,
            :time_to_full_charge, :inside_temp, :outside_temp,
            :is_climate_on, :locked, :software_version
        )
    """
    params = {**state, "recorded_at": _now()}
    with get_conn(db_path) as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def get_recent_snapshots(db_path: str | Path, limit: int = 500) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM state_snapshots ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_snapshot(db_path: str | Path) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM state_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None

# ─────────────────────────────────────────────────────────────────────────────
# Drive sessions
# ─────────────────────────────────────────────────────────────────────────────

def open_drive(db_path: str | Path, state: dict[str, Any]) -> int:
    sql = """
        INSERT INTO drives (vin, started_at, start_latitude, start_longitude, start_range)
        VALUES (:vin, :started_at, :latitude, :longitude, :battery_range)
    """
    with get_conn(db_path) as conn:
        cur = conn.execute(sql, {**state, "started_at": _now()})
        return cur.lastrowid


def close_drive(
    db_path: str | Path,
    drive_id: int,
    start_odometer: float | None,
    state: dict[str, Any],
) -> None:
    ended_at = _now()
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT started_at, start_range FROM drives WHERE id=?", (drive_id,)
        ).fetchone()
        started = datetime.fromisoformat(row["started_at"])
        ended   = datetime.fromisoformat(ended_at)
        dur_min = (ended - started).total_seconds() / 60

        dist = None
        if start_odometer and state.get("odometer"):
            dist = state["odometer"] - start_odometer

        start_range = row["start_range"] or 0
        end_range   = state.get("battery_range")
        range_used  = (start_range - end_range) if (start_range and end_range) else None

        # Efficiency: kWh used → Wh/mi
        eff = None
        if dist and dist > 0 and range_used and range_used > 0:
            # Tesla rates ~3.5 miles/kWh typically; use rated range delta as proxy kWh
            kwh_used = range_used * (1 / 3.5)   # rough but no onboard data
            eff = round((kwh_used * 1000) / dist, 1)

        conn.execute("""
            UPDATE drives SET
                ended_at=?, end_latitude=?, end_longitude=?,
                distance_miles=?, duration_min=?, end_range=?,
                range_used=?, efficiency_wh_mi=?
            WHERE id=?
        """, (
            ended_at,
            state.get("latitude"), state.get("longitude"),
            dist, dur_min, end_range, range_used, eff,
            drive_id,
        ))


def set_drive_address(
    db_path: str | Path,
    drive_id: int,
    start_address: str | None,
    end_address: str | None,
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE drives SET start_address=?, end_address=? WHERE id=?",
            (start_address, end_address, drive_id),
        )


def get_drives(db_path: str | Path, limit: int = 100, vin: str | None = None) -> list[dict]:
    sql = "SELECT * FROM drives"
    params: list[Any] = []
    if vin:
        sql += " WHERE vin=?"
        params.append(vin)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_drive(db_path: str | Path, drive_id: int) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM drives WHERE id=?", (drive_id,)).fetchone()
    return dict(row) if row else None

# ─────────────────────────────────────────────────────────────────────────────
# Waypoints
# ─────────────────────────────────────────────────────────────────────────────

def insert_waypoint(db_path: str | Path, drive_id: int, state: dict[str, Any]) -> None:
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO waypoints (drive_id, recorded_at, latitude, longitude, speed, power, odometer)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            drive_id, _now(),
            state.get("latitude"), state.get("longitude"),
            state.get("speed"), state.get("power"), state.get("odometer"),
        ))


def get_waypoints(db_path: str | Path, drive_id: int) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM waypoints WHERE drive_id=? ORDER BY id ASC", (drive_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_waypoints(db_path: str | Path, limit: int = 20000) -> list[dict]:
    """All waypoints for the lifetime driving map."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT latitude, longitude FROM waypoints WHERE latitude IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# Charge sessions
# ─────────────────────────────────────────────────────────────────────────────

def open_charge(db_path: str | Path, state: dict[str, Any]) -> int:
    sql = """
        INSERT INTO charges (
            vin, started_at, latitude, longitude,
            start_battery_level, charge_limit_soc
        ) VALUES (:vin, :started_at, :latitude, :longitude,
                  :battery_level, :charge_limit_soc)
    """
    with get_conn(db_path) as conn:
        cur = conn.execute(sql, {**state, "started_at": _now()})
        return cur.lastrowid


def close_charge(db_path: str | Path, charge_id: int, state: dict[str, Any]) -> None:
    ended_at = _now()
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT started_at FROM charges WHERE id=?", (charge_id,)
        ).fetchone()
        started  = datetime.fromisoformat(row["started_at"])
        ended    = datetime.fromisoformat(ended_at)
        dur_min  = (ended - started).total_seconds() / 60

        conn.execute("""
            UPDATE charges SET
                ended_at=?, end_battery_level=?, energy_added_kwh=?,
                miles_added=?, max_charger_power=?, duration_min=?
            WHERE id=?
        """, (
            ended_at,
            state.get("battery_level"),
            state.get("charge_energy_added"),
            state.get("charge_miles_added_rated"),
            state.get("charger_power"),
            dur_min,
            charge_id,
        ))


def set_charge_address(db_path: str | Path, charge_id: int, address: str | None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("UPDATE charges SET address=? WHERE id=?", (address, charge_id))


def get_charges(db_path: str | Path, limit: int = 100, vin: str | None = None) -> list[dict]:
    sql = "SELECT * FROM charges"
    params: list[Any] = []
    if vin:
        sql += " WHERE vin=?"
        params.append(vin)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# States (sleep / online / driving / charging)
# ─────────────────────────────────────────────────────────────────────────────

def open_state(db_path: str | Path, vin: str, state_name: str) -> int:
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO states (vin, state, started_at) VALUES (?,?,?)",
            (vin, state_name, _now()),
        )
        return cur.lastrowid


def close_state(db_path: str | Path, state_id: int) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE states SET ended_at=? WHERE id=?", (_now(), state_id)
        )


def get_states(db_path: str | Path, limit: int = 200, vin: str | None = None) -> list[dict]:
    sql = "SELECT * FROM states"
    params: list[Any] = []
    if vin:
        sql += " WHERE vin=?"
        params.append(vin)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# Software updates
# ─────────────────────────────────────────────────────────────────────────────

def record_software_update(db_path: str | Path, vin: str, version: str) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO software_updates (vin, version, recorded_at) VALUES (?,?,?)",
            (vin, version, _now()),
        )


def get_software_updates(db_path: str | Path, vin: str | None = None) -> list[dict]:
    sql = "SELECT * FROM software_updates"
    params: list[Any] = []
    if vin:
        sql += " WHERE vin=?"
        params.append(vin)
    sql += " ORDER BY id DESC"
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# Vampire drain helper
# ─────────────────────────────────────────────────────────────────────────────

def get_vampire_drain_events(db_path: str | Path, limit: int = 100) -> list[dict]:
    """
    Return parked periods where battery dropped without charging.
    Derived from consecutive snapshots where shift_state=P and battery_level drops.
    """
    sql = """
        WITH parked AS (
            SELECT
                recorded_at,
                battery_level,
                vin,
                LAG(battery_level) OVER (PARTITION BY vin ORDER BY id)   AS prev_bat,
                LAG(recorded_at)   OVER (PARTITION BY vin ORDER BY id)   AS prev_time
            FROM state_snapshots
            WHERE (shift_state = 'P' OR shift_state IS NULL)
              AND charging_state != 'Charging'
        )
        SELECT
            prev_time   AS started_at,
            recorded_at AS ended_at,
            prev_bat    AS start_battery,
            battery_level AS end_battery,
            ROUND(prev_bat - battery_level, 2) AS drain_pct,
            vin
        FROM parked
        WHERE prev_bat IS NOT NULL
          AND prev_bat - battery_level > 0.5
        ORDER BY recorded_at DESC
        LIMIT ?
    """
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# Battery health
# ─────────────────────────────────────────────────────────────────────────────

def get_battery_health(db_path: str | Path, vin: str | None = None) -> list[dict]:
    """
    Daily max battery_range when battery_level >= 95 → proxy for rated range at full charge.
    Degradation visible by comparing first vs recent values.
    """
    sql = """
        SELECT
            DATE(recorded_at) AS date,
            ROUND(MAX(battery_range), 1) AS max_range,
            ROUND(MAX(battery_level), 1) AS max_level
        FROM state_snapshots
        WHERE battery_level >= 90
          AND battery_range IS NOT NULL
          {vin_filter}
        GROUP BY DATE(recorded_at)
        ORDER BY date ASC
    """.format(vin_filter="AND vin=?" if vin else "")

    params = [vin] if vin else []
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# Aggregate stats
# ─────────────────────────────────────────────────────────────────────────────

def get_stats(db_path: str | Path, vin: str | None = None) -> dict[str, Any]:
    vin_filter = "WHERE vin=?" if vin else ""
    params = [vin] if vin else []

    with get_conn(db_path) as conn:
        drives  = conn.execute(
            f"SELECT * FROM drives {vin_filter} AND ended_at IS NOT NULL"
            if vin else "SELECT * FROM drives WHERE ended_at IS NOT NULL"
        , params).fetchall()
        charges = conn.execute(
            f"SELECT * FROM charges {vin_filter} AND ended_at IS NOT NULL"
            if vin else "SELECT * FROM charges WHERE ended_at IS NOT NULL"
        , params).fetchall()

    total_mi  = sum((r["distance_miles"]  or 0) for r in drives)
    total_kwh = sum((r["energy_added_kwh"] or 0) for r in charges)

    efficiencies = [r["efficiency_wh_mi"] for r in drives if r["efficiency_wh_mi"]]
    avg_eff = round(sum(efficiencies) / len(efficiencies), 1) if efficiencies else None

    return {
        "total_drives":    len(drives),
        "total_miles":     round(total_mi, 1),
        "total_charges":   len(charges),
        "total_kwh":       round(total_kwh, 1),
        "avg_drive_miles": round(total_mi / len(drives), 1) if drives else 0,
        "avg_charge_kwh":  round(total_kwh / len(charges), 1) if charges else 0,
        "avg_efficiency_wh_mi": avg_eff,
    }
