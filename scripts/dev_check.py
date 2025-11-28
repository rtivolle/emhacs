"""
Quick manual check script for the Emporia Vehicle/Charger integration.

Usage:
    EMPORIA_EMAIL="user@example.com" EMPORIA_PASSWORD="supersecret" python scripts/dev_check.py
"""

import os
import sys

from pyemvue import PyEmVue


def main() -> int:
    email = os.environ.get("EMPORIA_EMAIL")
    password = os.environ.get("EMPORIA_PASSWORD")
    if not email or not password:
        print("Set EMPORIA_EMAIL and EMPORIA_PASSWORD environment variables before running.")
        return 1

    vue = PyEmVue()
    logged_in = vue.login(email=email, password=password)
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
        print(
            f"- Charger {c.device_gid}: status={c.status or 'unknown'} "
            f"on={c.charger_on} rate={c.charging_rate}/{c.max_charging_rate}"
        )

    print("Check complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
