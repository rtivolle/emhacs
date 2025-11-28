import asyncio
import logging
from datetime import timedelta

from pyemvue import PyEmVue
from pyemvue.device import ChargerDevice, Vehicle, VueDevice
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, VUE_DATA, UPDATE_INTERVAL_SECONDS

# Update interval - too frequent will hit Emporia limits.
SCAN_INTERVAL = timedelta(seconds=UPDATE_INTERVAL_SECONDS)

_LOGGER: logging.Logger = logging.getLogger(__name__)

charger_devices: dict[int, VueDevice] = {}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add vehicles in HA."""

    # Get the Vue client that was set up in __init__.py.
    vue: PyEmVue = hass.data[DOMAIN][config_entry.entry_id][VUE_DATA]

    charger_devices.clear()

    loop = asyncio.get_event_loop()
    vehicles = await loop.run_in_executor(None, vue.get_vehicles)
    devices = await loop.run_in_executor(None, vue.get_devices)

    # Build a quick lookup for charger metadata (names live on VueDevice).
    for dev in devices:
        if dev.ev_charger:
            charger_devices[dev.device_gid] = dev

    # Set up sensors for each vehicle.
    vehicle_sensors = []
    for vehicle in vehicles:
        vehicle_sensors.append(VehicleSensor(vue, vehicle))
    _LOGGER.info("Monitoring %s vehicles", len(vehicle_sensors))

    sensors = vehicle_sensors
    # Add a status sensor for each Emporia EV charger.
    try:
        (_, chargers) = await loop.run_in_executor(
            None, lambda: vue.get_devices_status(devices)
        )
        for charger in chargers:
            sensors.append(ChargerStatusSensor(vue, charger))
        if chargers:
            _LOGGER.info("Monitoring %s chargers", len(chargers))
        else:
            _LOGGER.info("No chargers found for this account")
    except Exception as err:
        _LOGGER.warning("Unable to load charger status from Emporia: %s", err)

    async_add_entities(sensors, True)


class VehicleSensor(SensorEntity):
    """Representation of a Vehicle Battery Sensor."""
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, vue_client: PyEmVue, vehicle: Vehicle):
        # Creates a sensor for the vehicle.
        self.vue = vue_client
        self.vehicle = vehicle

    def update(self) -> None:
        # Update battery level and additional attributes from Emporia API.
        lastVehicleStatus = self.vue.get_vehicle_status(self.vehicle.vehicle_gid)
        self.battery_level = lastVehicleStatus.battery_level
        self.extra_attributes = lastVehicleStatus.as_dictionary()
        _LOGGER.debug(
            "Fetched vehicle status for vehicle %s - battery level %s",
            self.vehicle,
            lastVehicleStatus.battery_level,
        )

    @property
    def native_value(self) -> str | None:
        return self.battery_level

    @property
    def name(self) -> str:
        return self.vehicle.display_name

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return self.extra_attributes

    @property
    def unique_id(self):
        """Unique ID for the vehicle"""
        return f"sensor.vehiclevue.{self.vehicle.vehicle_gid}"

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {
                # Unique identifiers within a specific domain
                (DOMAIN, self.vehicle.vehicle_gid)
            },
            "name": self.vehicle.display_name
        }


class ChargerStatusSensor(SensorEntity):
    """Text sensor describing Emporia EV charger status."""

    _attr_icon = "mdi:ev-station"

    def __init__(self, vue_client: PyEmVue, charger: ChargerDevice) -> None:
        self.vue = vue_client
        self.charger = charger
        self._state = None
        self.extra_attributes: dict[str, object] = {}
        self._charger_name = self._get_charger_name(charger.device_gid)

    def _get_charger_name(self, charger_gid: int) -> str:
        device = charger_devices.get(charger_gid)
        if device:
            return (
                device.display_name
                or device.device_name
                or device.manufacturer_id
                or f"Charger {charger_gid}"
            )
        return f"Charger {charger_gid}"

    def update(self) -> None:
        # Fetch latest charger state from Emporia.
        devices = list(charger_devices.values()) if charger_devices else None
        try:
            (_, chargers) = self.vue.get_devices_status(devices)
            updated = next(
                (c for c in chargers if c.device_gid == self.charger.device_gid), None
            )
            if updated:
                self.charger = updated
        except Exception as err:
            _LOGGER.warning(
                "Unable to refresh charger %s status: %s", self.charger.device_gid, err
            )

        self._state = self.charger.status or (
            "on" if self.charger.charger_on else "off"
        )
        self.extra_attributes = {
            "charger_on": self.charger.charger_on,
            "message": self.charger.message,
            "icon_label": self.charger.icon_label,
            "icon_detail_text": self.charger.icon_detail_text,
            "fault_text": self.charger.fault_text,
            "charging_rate": self.charging_rate_display,
            "max_charging_rate": self.charger.max_charging_rate,
            "load_gid": self.charger.load_gid,
            "pro_control_code": self.charger.pro_control_code,
            "debug_code": self.charger.debug_code,
        }
        _LOGGER.debug(
            "Fetched charger status for %s - state: %s rate: %s/%s",
            self.charger.device_gid,
            self._state,
            self.charger.charging_rate,
            self.charger.max_charging_rate,
        )

    @property
    def native_value(self) -> str | None:
        return self._state

    @property
    def name(self) -> str:
        return f"{self._charger_name} Charger Status"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return self.extra_attributes

    @property
    def unique_id(self):
        """Unique ID for the charger."""
        return f"sensor.vehiclevue.charger.{self.charger.device_gid}"

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"charger-{self.charger.device_gid}")},
            "name": self._charger_name,
        }

    @property
    def charging_rate_display(self) -> str | int:
        """Return a friendly charging rate value."""
        # charging_rate unit is reported directly from Emporia; keep raw.
        return self.charger.charging_rate
