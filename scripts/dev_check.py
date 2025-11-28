"""
Quick manual check script for the Emporia Vehicle/Charger integration.

Usage:
    EMPORIA_EMAIL="user@example.com" EMPORIA_PASSWORD="supersecret" python scripts/dev_check.py
"""

import os
import sys

from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit


def live_power_kw(vue: PyEmVue, charger_gid: int):
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
    email = os.environ.get("EMPORIA_EMAIL")
    password = os.environ.get("EMPORIA_PASSWORD")
    if not email or not password:
        print("Set EMPORIA_EMAIL and EMPORIA_PASSWORD environment variables before running.")
        return 1

    vue = PyEmVue()
    logged_in = vue.login(username=email, password=password)
    if not logged_in:
        print("Login failed, check credentials.")
        return 1

    print("Connected to Emporia.")

    vehicles = vue.get_vehicles()
    print(f"Found {len(vehicles)} vehicle(s).")
    for v in vehicles:
        try:
            status = vue.get_vehicle_status(v.vehicle_gid)
            print(
                f"- {v.display_name}: battery={status.battery_level}% "
                f"state={status.vehicle_state} charging={status.charging_state}"
            )
        except Exception as err:  # noqa: BLE001
            print(f"- {v.display_name}: unable to fetch status ({err})")

    devices = vue.get_devices()
    (_, chargers) = vue.get_devices_status(devices)
    print(f"Found {len(chargers)} charger(s).")
    for c in chargers:
        live_kw = None
        try:
            live_kw = live_power_kw(vue, c.device_gid)
        except Exception as err:  # noqa: BLE001
            print(f"  unable to fetch live power for charger {c.device_gid}: {err}")
        kw = round((c.charging_rate or 0) * 240 / 1000, 3)
        print(
            f"- Charger {c.device_gid}: status={c.status or 'unknown'} "
            f"on={c.charger_on} rate={c.charging_rate}A (est {kw} kW) "
            f"live_power={live_kw if live_kw is not None else 'n/a'} kW"
        )

    print("Check complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
