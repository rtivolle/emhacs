"""
Prompt for Emporia credentials, log in, and print live charger values.

Usage:
    python scripts/verify_charger.py
"""

from __future__ import annotations

import getpass
import sys
from typing import Optional

from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

ASSUMED_VOLTAGE = 240  # volts, used to estimate kW from amps


def prompt_for_credentials() -> tuple[str, str]:
    email = input("Emporia email: ").strip()
    password = getpass.getpass("Emporia password: ").strip()
    return email, password


def estimate_kw(amps: Optional[float]) -> Optional[float]:
    if amps is None:
        return None
    return round((amps * ASSUMED_VOLTAGE) / 1000, 3)


def live_power_kw(vue: PyEmVue, charger_gid: int) -> Optional[float]:
    """Use usage endpoint (1-second scale) to calculate live kW draw."""
    usage = vue.get_device_list_usage(
        [charger_gid], None, scale=Scale.SECOND.value, unit=Unit.KWH.value
    )
    device_usage = usage.get(charger_gid)
    if not device_usage:
        return None
    total_kwh = 0.0
    has_data = False
    for channel in device_usage.channels.values():
        if channel.usage is not None:
            total_kwh += channel.usage
            has_data = True
    if not has_data:
        return None
    return round(total_kwh * 3600, 3)


def main() -> int:
    email, password = prompt_for_credentials()
    if not email or not password:
        print("Email and password are required.")
        return 1

    vue = PyEmVue()
    if not vue.login(username=email, password=password):
        print("Login failed. Check credentials and try again.")
        return 1

    devices = vue.get_devices()
    (_, chargers) = vue.get_devices_status(devices)
    if not chargers:
        print("No chargers found on this account.")
        return 0

    print(f"Found {len(chargers)} charger(s):")
    for charger in chargers:
        amps = charger.charging_rate
        kw = estimate_kw(amps)
        live_kw = None
        try:
            live_kw = live_power_kw(vue, charger.device_gid)
        except Exception as err:  # noqa: BLE001
            print(f"  unable to fetch live power for charger {charger.device_gid}: {err}")
        print(
            f"- Charger {charger.device_gid}: "
            f"status={charger.status or 'unknown'}, "
            f"on={charger.charger_on}, "
            f"rate={amps if amps is not None else 'n/a'}A, "
            f"est_power={kw if kw is not None else 'n/a'} kW, "
            f"live_power={live_kw if live_kw is not None else 'n/a'} kW, "
            f"max_rate={charger.max_charging_rate}A"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
