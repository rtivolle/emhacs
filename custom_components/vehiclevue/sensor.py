import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from pyemvue import PyEmVue
from pyemvue.device import ChargerDevice, Vehicle, VueDevice
from pyemvue.enums import Scale, Unit
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN, VUE_DATA, UPDATE_INTERVAL_SECONDS

# Update interval - too frequent will hit Emporia limits.
SCAN_INTERVAL = timedelta(seconds=UPDATE_INTERVAL_SECONDS)
# Default voltage to convert charger amps to kW when Emporia only provides amps.
ASSUMED_VOLTAGE = 240

_LOGGER: logging.Logger = logging.getLogger(__name__)

charger_devices: dict[int, VueDevice] = {}


@dataclass
class VehicleVueCoordinatorData:
    """Container for Emporia vehicle/charger data."""

    vehicle_status: dict[int, object]
    chargers: dict[int, ChargerDevice]
    charger_power_kw: dict[int, float | None]


class VehicleVueDataCoordinator(DataUpdateCoordinator[VehicleVueCoordinatorData]):
    """Coordinator to poll Emporia vehicle + charger state."""

    def __init__(
        self,
        hass: HomeAssistant,
        vue_client: PyEmVue,
        vehicles: list[Vehicle],
        chargers: list[VueDevice],
    ) -> None:
        self.vue = vue_client
        self._vehicles = vehicles
        self._chargers = chargers
        super().__init__(
            hass,
            _LOGGER,
            name="VehicleVue data",
            update_interval=SCAN_INTERVAL,
        )

    def _fetch_vehicle_status(self) -> dict[int, object]:
        statuses: dict[int, object] = {}
        for vehicle in self._vehicles:
            statuses[vehicle.vehicle_gid] = self.vue.get_vehicle_status(
                vehicle.vehicle_gid
            )
        return statuses

    def _fetch_charger_status(self) -> dict[int, ChargerDevice]:
        if not self._chargers:
            return {}
        (_, chargers) = self.vue.get_devices_status(self._chargers)
        return {charger.device_gid: charger for charger in chargers}

    async def _async_update_data(self) -> VehicleVueCoordinatorData:
        loop = asyncio.get_event_loop()
        try:
            vehicle_status = await loop.run_in_executor(
                None, self._fetch_vehicle_status
            )
            chargers = await loop.run_in_executor(None, self._fetch_charger_status)
        except Exception as err:
            raise UpdateFailed(f"Error talking to Emporia: {err}") from err

        charger_power: dict[int, float | None] = {}
        for gid in chargers:
            try:
                charger_power[gid] = await loop.run_in_executor(
                    None, _get_live_power_kw, self.vue, gid
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Unable to fetch live power for charger %s: %s", gid, err
                )
                charger_power[gid] = None

        return VehicleVueCoordinatorData(
            vehicle_status=vehicle_status,
            chargers=chargers,
            charger_power_kw=charger_power,
        )


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

    coordinator = VehicleVueDataCoordinator(
        hass, vue, vehicles, list(charger_devices.values())
    )
    await coordinator.async_config_entry_first_refresh()

    vehicle_sensors = [VehicleSensor(coordinator, vehicle) for vehicle in vehicles]
    _LOGGER.info("Monitoring %s vehicles", len(vehicle_sensors))

    chargers = (
        list(coordinator.data.chargers.values()) if coordinator.data else []
    )
    charger_sensors: list[SensorEntity] = []
    for charger in chargers:
        charger_sensors.append(
            ChargerStatusSensor(coordinator, charger.device_gid)
        )
        charger_sensors.append(
            ChargerPowerSensor(coordinator, charger.device_gid)
        )

    if chargers:
        _LOGGER.info("Monitoring %s chargers", len(chargers))
    else:
        _LOGGER.info("No chargers found for this account")

    async_add_entities([*vehicle_sensors, *charger_sensors])


class VehicleSensor(CoordinatorEntity[VehicleVueCoordinatorData], SensorEntity):
    """Representation of a Vehicle Battery Sensor."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_should_poll = False

    def __init__(
        self, coordinator: VehicleVueDataCoordinator, vehicle: Vehicle
    ) -> None:
        super().__init__(coordinator)
        self.vehicle = vehicle
        self._attr_unique_id = f"sensor.vehiclevue.{self.vehicle.vehicle_gid}"
        self._attr_name = self.vehicle.display_name

    def _latest_status(self):
        if not self.coordinator.data:
            return None
        return self.coordinator.data.vehicle_status.get(self.vehicle.vehicle_gid)

    @property
    def native_value(self) -> str | None:
        status = self._latest_status()
        return status.battery_level if status else None

    @property
    def name(self) -> str:
        return self.vehicle.display_name

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        status = self._latest_status()
        return status.as_dictionary() if status else {}

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


class ChargerStatusSensor(
    CoordinatorEntity[VehicleVueCoordinatorData], SensorEntity
):
    """Text sensor describing Emporia EV charger status."""

    _attr_icon = "mdi:ev-station"
    _attr_should_poll = False

    def __init__(
        self, coordinator: VehicleVueDataCoordinator, charger_gid: int
    ) -> None:
        super().__init__(coordinator)
        self._charger_gid = charger_gid
        self._charger_name = self._get_charger_name(charger_gid)
        self._attr_unique_id = f"sensor.vehiclevue.charger.{charger_gid}"

    def _charger(self) -> ChargerDevice | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.chargers.get(self._charger_gid)

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

    @property
    def native_value(self) -> str | None:
        charger = self._charger()
        if not charger:
            return None
        return charger.status or ("on" if charger.charger_on else "off")

    @property
    def name(self) -> str:
        return f"{self._charger_name} Charger Status"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        charger = self._charger()
        if not charger:
            return {}
        live_kw = None
        if self.coordinator.data:
            live_kw = self.coordinator.data.charger_power_kw.get(self._charger_gid)
        return {
            "charger_on": charger.charger_on,
            "message": charger.message,
            "icon_label": charger.icon_label,
            "icon_detail_text": charger.icon_detail_text,
            "fault_text": charger.fault_text,
            "charging_rate": self.charging_rate_display,
            "max_charging_rate": charger.max_charging_rate,
            "load_gid": charger.load_gid,
            "pro_control_code": charger.pro_control_code,
            "debug_code": charger.debug_code,
            "live_power_kw": live_kw,
        }

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"charger-{self._charger_gid}")},
            "name": self._charger_name,
        }

    @property
    def charging_rate_display(self) -> str | int:
        """Return a friendly charging rate value."""
        # charging_rate unit is reported directly from Emporia; keep raw.
        charger = self._charger()
        return charger.charging_rate if charger else "unknown"


class ChargerPowerSensor(
    CoordinatorEntity[VehicleVueCoordinatorData], SensorEntity
):
    """Power sensor for Emporia EV charger (current charging rate)."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_icon = "mdi:flash"
    _attr_should_poll = False

    def __init__(
        self, coordinator: VehicleVueDataCoordinator, charger_gid: int
    ) -> None:
        super().__init__(coordinator)
        self._charger_gid = charger_gid
        self._charger_name = self._get_charger_name(charger_gid)
        self._attr_unique_id = f"sensor.vehiclevue.charger.power.{charger_gid}"

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

    def _charger(self) -> ChargerDevice | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.chargers.get(self._charger_gid)

    @property
    def native_value(self) -> float | None:
        charger = self._charger()
        if not charger:
            return None
        live_kw = None
        if self.coordinator.data:
            live_kw = self.coordinator.data.charger_power_kw.get(self._charger_gid)
        return live_kw if live_kw is not None else self._calculate_kw(
            charger.charging_rate
        )

    @property
    def name(self) -> str:
        return f"{self._charger_name} Charging Power"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        charger = self._charger()
        if not charger:
            return {}
        live_kw = None
        if self.coordinator.data:
            live_kw = self.coordinator.data.charger_power_kw.get(self._charger_gid)
        return {
            "max_charging_rate": charger.max_charging_rate,
            "charger_on": charger.charger_on,
            "status": charger.status,
            "raw_charging_rate_amps": charger.charging_rate,
            "assumed_voltage": ASSUMED_VOLTAGE,
            "live_power_kw": live_kw,
        }

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"charger-{self._charger_gid}")},
            "name": self._charger_name,
        }

    def _calculate_kw(self, charging_rate: float | None) -> float | None:
        if charging_rate is None:
            return None
        # Emporia charger reports amps; convert to kW assuming split-phase ~240V.
        return round((charging_rate * ASSUMED_VOLTAGE) / 1000, 3)


def _get_live_power_kw(vue: PyEmVue, charger_gid: int) -> float | None:
    """Return live charger power in kW using the usage endpoint (1-second scale)."""
    try:
        usage = vue.get_device_list_usage(
            [charger_gid], None, scale=Scale.SECOND.value, unit=Unit.KWH.value
        )
    except Exception as err:
        _LOGGER.debug("Unable to fetch live power for charger %s: %s", charger_gid, err)
        return None

    device_usage = usage.get(charger_gid)
    if not device_usage:
        return None

    # Sum all channel usages (kWh consumed over the 1-second interval).
    total_kwh = 0.0
    has_data = False
    for channel in device_usage.channels.values():
        if channel.usage is not None:
            total_kwh += channel.usage
            has_data = True
    if not has_data:
        return None

    # Convert 1-second kWh consumption to kW (kWh * 3600).
    return round(total_kwh * 3600, 3)
