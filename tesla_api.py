"""
tesla_api.py
------------
Thin wrapper around teslapy for authentication and vehicle data fetching.

teslapy handles the OAuth 2.0 + MFA flow and caches tokens in a local JSON
file so you only need to authenticate once.
"""

from __future__ import annotations

import logging
from typing import Any

import teslapy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth / session
# ---------------------------------------------------------------------------

def get_tesla_session(email: str) -> teslapy.Tesla:
    """
    Return an authenticated Tesla session.

    On first run this opens a browser for the OAuth login.  After that the
    token is cached in ~/.tesla/cache.json (managed by teslapy) and reused.
    """
    tesla = teslapy.Tesla(email)
    if not tesla.authorized:
        logger.info("No cached token found — starting OAuth login flow …")
        tesla.refresh_token(refresh_token=None)   # triggers browser login
    return tesla


# ---------------------------------------------------------------------------
# Vehicle helpers
# ---------------------------------------------------------------------------

def list_vehicles(tesla: teslapy.Tesla) -> list[teslapy.Vehicle]:
    """Return all vehicles on the account."""
    return tesla.vehicle_list()


def get_vehicle_state(vehicle: teslapy.Vehicle) -> dict[str, Any] | None:
    """
    Wake the vehicle if needed and return a combined state snapshot.

    Returns None if the vehicle cannot be woken (e.g. it's offline and deep
    sleeping) so the caller can skip this poll cycle.
    """
    try:
        # Wake the car if it's asleep (times out after ~30 s by default)
        vehicle.sync_wake_up()
    except teslapy.VehicleError as exc:
        logger.warning("Could not wake vehicle: %s", exc)
        return None

    try:
        data = vehicle.get_vehicle_data()
    except teslapy.VehicleError as exc:
        logger.error("Failed to fetch vehicle data: %s", exc)
        return None

    return _parse_state(data)


# ---------------------------------------------------------------------------
# Data parsing
# ---------------------------------------------------------------------------

def _parse_state(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Flatten the nested Tesla API response into a single, easy-to-use dict.

    Only the fields we actually store / display are extracted here; extend as
    you need.
    """
    ds = raw.get("drive_state", {})
    cs = raw.get("charge_state", {})
    vs = raw.get("vehicle_state", {})
    climate = raw.get("climate_state", {})

    return {
        # ── Identity ─────────────────────────────────────────────────────
        "vin": raw.get("vin"),
        "display_name": raw.get("display_name"),

        # ── Drive state ──────────────────────────────────────────────────
        "shift_state": ds.get("shift_state"),          # P / D / R / N / None
        "speed": ds.get("speed"),                      # mph
        "latitude": ds.get("latitude"),
        "longitude": ds.get("longitude"),
        "heading": ds.get("heading"),
        "gps_as_of": ds.get("gps_as_of"),              # unix timestamp
        "power": ds.get("power"),                      # kW (+ = discharge, - = regen)
        "odometer": vs.get("odometer"),                # miles

        # ── Charge state ─────────────────────────────────────────────────
        "battery_level": cs.get("battery_level"),          # %
        "battery_range": cs.get("battery_range"),          # miles (EPA estimate)
        "est_battery_range": cs.get("est_battery_range"),  # miles (real-world)
        "charge_rate": cs.get("charge_rate"),              # mph
        "charger_power": cs.get("charger_power"),          # kW
        "charging_state": cs.get("charging_state"),        # Charging / Disconnected / etc.
        "charge_limit_soc": cs.get("charge_limit_soc"),   # %
        "time_to_full_charge": cs.get("time_to_full_charge"),  # hours
        "charge_energy_added": cs.get("charge_energy_added"),  # kWh
        "charge_miles_added_rated": cs.get("charge_miles_added_rated"),

        # ── Climate ──────────────────────────────────────────────────────
        "inside_temp": climate.get("inside_temp"),     # °C
        "outside_temp": climate.get("outside_temp"),   # °C
        "is_climate_on": climate.get("is_climate_on"),

        # ── Software ─────────────────────────────────────────────────────
        "software_version": vs.get("car_version"),
        "locked": vs.get("locked"),

        # ── Timestamp ────────────────────────────────────────────────────
        "timestamp": ds.get("timestamp"),              # ms epoch
    }
