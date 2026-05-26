"""
poller.py  –  Smart background poller for TeslaLogger

Car state machine
─────────────────
  asleep  ──── wakes up ────────────────────────────► online
  online  ──── shift D/R/N ─────────────────────────► driving
  online  ──── charging_state=Charging ─────────────► charging
  driving ──── shift=P and speed=0 ─────────────────► online
  charging ─── charging_state≠Charging ─────────────► online
  online  ──── idle for N polls ────────────────────► (back to sleep check)

Poll intervals
──────────────
  driving / charging : 15 s  (high precision)
  online (idle)      : 60 s  (don't wear the API)
  checking sleep     : 600 s (10 min, no wake attempt)

Vampire drain prevention
────────────────────────
  We NEVER call sync_wake_up() when the car is in 'asleep' state.
  We use vehicle.get_vehicle_summary() (no wake) to check for transitions.
"""

from __future__ import annotations

import logging
import time
import threading
import urllib.request
import urllib.parse
import json
from dataclasses import dataclass, field
from typing import Any

import teslapy

import database as db
import tesla_api as api

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Reverse geocoding (OpenStreetMap Nominatim, free, no key)
# ─────────────────────────────────────────────────────────────────────────────

_geo_cache: dict[tuple, str] = {}


def reverse_geocode(lat: float | None, lon: float | None) -> str | None:
    """Return a human-readable address for a lat/lon pair."""
    if lat is None or lon is None:
        return None
    key = (round(lat, 4), round(lon, 4))
    if key in _geo_cache:
        return _geo_cache[key]
    try:
        url = (
            "https://nominatim.openstreetmap.org/reverse"
            f"?lat={lat}&lon={lon}&format=json&zoom=16"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "TeslaLogger/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        addr = data.get("display_name", "")
        # Trim to city-level: "123 Main St, Cupertino, CA, US"
        parts = [p.strip() for p in addr.split(",")]
        short = ", ".join(parts[:3]) if len(parts) >= 3 else addr
        _geo_cache[key] = short
        return short
    except Exception as exc:
        log.debug("Geocode failed for %s,%s: %s", lat, lon, exc)
        return None


def _geocode_drive_async(db_path: str, drive_id: int,
                          start_lat: float, start_lon: float,
                          end_lat: float, end_lon: float) -> None:
    def _run():
        s = reverse_geocode(start_lat, start_lon)
        e = reverse_geocode(end_lat, end_lon)
        db.set_drive_address(db_path, drive_id, s, e)
        log.debug("Drive %d geocoded: %s → %s", drive_id, s, e)
    threading.Thread(target=_run, daemon=True).start()


def _geocode_charge_async(db_path: str, charge_id: int,
                           lat: float, lon: float) -> None:
    def _run():
        a = reverse_geocode(lat, lon)
        db.set_charge_address(db_path, charge_id, a)
        log.debug("Charge %d geocoded: %s", charge_id, a)
    threading.Thread(target=_run, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Per-vehicle session tracker
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VehicleTracker:
    vin: str
    # session ids
    drive_id:     int | None = None
    charge_id:    int | None = None
    state_id:     int | None = None
    # cached values
    start_odometer: float | None = None
    drive_start_lat: float | None = None
    drive_start_lon: float | None = None
    charge_start_lat: float | None = None
    charge_start_lon: float | None = None
    current_state: str = "online"   # asleep | online | driving | charging
    last_software: str | None = None
    idle_polls:    int = 0          # consecutive idle polls
    consecutive_errors: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_driving(state: dict) -> bool:
    return state.get("shift_state") in ("D", "R", "N")


def _is_charging(state: dict) -> bool:
    return state.get("charging_state") == "Charging"


def _transition_state(db_path: str, tracker: VehicleTracker, new_state: str) -> None:
    """Close the old state row, open a new one."""
    if tracker.state_id is not None:
        db.close_state(db_path, tracker.state_id)
    tracker.state_id   = db.open_state(db_path, tracker.vin, new_state)
    tracker.current_state = new_state
    log.info("[%s] state → %s", tracker.vin[-6:], new_state)


# ─────────────────────────────────────────────────────────────────────────────
# Single poll for one vehicle
# ─────────────────────────────────────────────────────────────────────────────

def poll_vehicle(
    vehicle: teslapy.Vehicle,
    db_path: str,
    tracker: VehicleTracker,
) -> float:
    """
    Poll the vehicle once. Returns the recommended sleep interval (seconds)
    for the NEXT poll.
    """
    # ── Check if the car is available without waking it ──────────────────────
    try:
        summary = vehicle.get_vehicle_summary()
        car_state = (summary.get("state") or "").lower()  # "online" | "asleep" | "offline"
    except Exception as exc:
        log.warning("[%s] Could not get summary: %s", tracker.vin[-6:], exc)
        tracker.consecutive_errors += 1
        return min(60 * 2 ** tracker.consecutive_errors, 600)

    # ── Car is asleep ─────────────────────────────────────────────────────────
    if car_state == "asleep":
        if tracker.current_state != "asleep":
            _transition_state(db_path, tracker, "asleep")
        tracker.idle_polls = 0
        tracker.consecutive_errors = 0
        return 600   # check again in 10 min, don't wake it

    # ── Car is online – fetch full state ──────────────────────────────────────
    try:
        vehicle.sync_wake_up()
        raw = api.get_vehicle_state(vehicle)
    except Exception as exc:
        log.warning("[%s] Wake/fetch failed: %s", tracker.vin[-6:], exc)
        tracker.consecutive_errors += 1
        return min(60 * 2 ** tracker.consecutive_errors, 300)

    if raw is None:
        tracker.consecutive_errors += 1
        return 60

    tracker.consecutive_errors = 0

    # ── Software version change detection ─────────────────────────────────────
    ver = raw.get("software_version")
    if ver and ver != tracker.last_software:
        if tracker.last_software is not None:   # skip very first poll
            db.record_software_update(db_path, tracker.vin, ver)
            log.info("[%s] Software update detected: %s", tracker.vin[-6:], ver)
        tracker.last_software = ver

    # ── Persist snapshot ──────────────────────────────────────────────────────
    db.insert_snapshot(db_path, raw)

    driving  = _is_driving(raw)
    charging = _is_charging(raw)

    # ── Drive session ─────────────────────────────────────────────────────────
    if driving and tracker.drive_id is None:
        tracker.drive_id        = db.open_drive(db_path, raw)
        tracker.start_odometer  = raw.get("odometer")
        tracker.drive_start_lat = raw.get("latitude")
        tracker.drive_start_lon = raw.get("longitude")
        tracker.idle_polls      = 0
        _transition_state(db_path, tracker, "driving")
        log.info("[%s] Drive started (id=%d)", tracker.vin[-6:], tracker.drive_id)

    if driving and tracker.drive_id is not None:
        # Record waypoint every poll while driving
        db.insert_waypoint(db_path, tracker.drive_id, raw)

        # Track max speed on the drive row (update in-place)
        spd = raw.get("speed") or 0
        with db.get_conn(db_path) as conn:
            conn.execute(
                "UPDATE drives SET max_speed=MAX(COALESCE(max_speed,0),?) WHERE id=?",
                (spd, tracker.drive_id),
            )

    if not driving and tracker.drive_id is not None:
        end_lat = raw.get("latitude")
        end_lon = raw.get("longitude")
        db.close_drive(db_path, tracker.drive_id, tracker.start_odometer, raw)
        _geocode_drive_async(
            db_path, tracker.drive_id,
            tracker.drive_start_lat or 0, tracker.drive_start_lon or 0,
            end_lat or 0, end_lon or 0,
        )
        log.info("[%s] Drive ended (id=%d)", tracker.vin[-6:], tracker.drive_id)
        tracker.drive_id       = None
        tracker.start_odometer = None
        tracker.drive_start_lat = None
        tracker.drive_start_lon = None

    # ── Charge session ────────────────────────────────────────────────────────
    if charging and tracker.charge_id is None:
        tracker.charge_id        = db.open_charge(db_path, raw)
        tracker.charge_start_lat = raw.get("latitude")
        tracker.charge_start_lon = raw.get("longitude")
        _transition_state(db_path, tracker, "charging")
        log.info("[%s] Charge started (id=%d)", tracker.vin[-6:], tracker.charge_id)

    if not charging and tracker.charge_id is not None:
        db.close_charge(db_path, tracker.charge_id, raw)
        _geocode_charge_async(
            db_path, tracker.charge_id,
            tracker.charge_start_lat or 0, tracker.charge_start_lon or 0,
        )
        log.info("[%s] Charge ended (id=%d)", tracker.vin[-6:], tracker.charge_id)
        tracker.charge_id = None

    # ── Idle detection → transition back to 'online' ──────────────────────────
    if not driving and not charging:
        if tracker.current_state != "online":
            _transition_state(db_path, tracker, "online")
        tracker.idle_polls += 1
    else:
        tracker.idle_polls = 0

    # ── Choose next interval ──────────────────────────────────────────────────
    if driving or charging:
        return 15    # high-frequency during active sessions
    elif tracker.idle_polls > 4:
        return 90    # car is idle online – slow down
    else:
        return 30    # default


# ─────────────────────────────────────────────────────────────────────────────
# Main poller entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_poller(email: str, db_path: str, default_interval: int = 30) -> None:
    """
    Main blocking loop.  Tracks ALL vehicles on the account simultaneously,
    each in its own thread.
    """
    db.init_db(db_path)
    log.info("Authenticating with Tesla API …")
    tesla = api.get_tesla_session(email)
    vehicles = api.list_vehicles(tesla)

    if not vehicles:
        log.error("No vehicles found on this account.")
        return

    log.info("Found %d vehicle(s): %s",
             len(vehicles),
             ", ".join(v.get("display_name", v.get("vin", "?")) for v in vehicles))

    def _vehicle_loop(vehicle: teslapy.Vehicle) -> None:
        vin     = vehicle.get("vin", "UNKNOWN")
        tracker = VehicleTracker(vin=vin)
        interval = default_interval

        while True:
            try:
                interval = poll_vehicle(vehicle, db_path, tracker)
            except KeyboardInterrupt:
                break
            except Exception as exc:
                log.error("[%s] Unhandled error: %s", vin[-6:], exc, exc_info=True)
                interval = 120
            time.sleep(interval)

    # Spawn one thread per vehicle
    threads = []
    for v in vehicles:
        t = threading.Thread(
            target=_vehicle_loop,
            args=(v,),
            daemon=True,
            name=f"poller-{v.get('vin', 'X')[-6:]}",
        )
        t.start()
        threads.append(t)
        log.info("Poller started for %s", v.get("display_name", v.get("vin")))

    # Block until interrupted
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        log.info("Poller stopped.")
