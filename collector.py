from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily

from .client import RedfishClient
from .config import Config, Target

LOG = logging.getLogger(__name__)
HEALTH = {"OK": 1, "Warning": 0.5, "Critical": 0, "Unknown": -1}
POWER_STATE = {"On": 1, "PoweringOn": .5, "PoweringOff": .5, "Off": 0}


def _uri(obj: dict, key: str) -> str | None:
    value = obj.get(key)
    return value.get("@odata.id") if isinstance(value, dict) else None


def _text(value) -> str:
    return "" if value is None else str(value)


def _status_samples(target: str, resource_type: str, items: Iterable[dict], chassis_id: str = ""):
    """Export the SC3200 health/health-rollup value for each resource."""
    for item in items:
        labels = [target, chassis_id, resource_type, _text(item.get("Id", "unknown")),
                  _text(item.get("Name", "unknown"))]
        status = item.get("Status") or {}
        health = status.get("Health") or status.get("HealthRollup") or "Unknown"
        yield "redfish_resource_health", labels, float(HEALTH.get(health, -1))


def _power_state_samples(target: str, resource_type: str, items: Iterable[dict], chassis_id: str = ""):
    for item in items:
        if item.get("PowerState") is not None:
            labels = [target, chassis_id, resource_type, _text(item.get("Id", "unknown"))]
            yield "redfish_resource_power_state", labels, float(POWER_STATE.get(item["PowerState"], -1))


def _sensor_samples(target: str, chassis_id: str, sensors: Iterable[dict]):
    """Map Sensor readings to explicit base units; export percent as ratio 0..1."""
    for sensor in sensors:
        reading = sensor.get("Reading")
        if reading is None:
            continue
        labels = [target, chassis_id, _text(sensor.get("Id", "unknown")),
                  _text(sensor.get("Name", sensor.get("Id", "unknown"))),
                  _text(sensor.get("PhysicalContext"))]
        kind, units = sensor.get("ReadingType"), sensor.get("ReadingUnits")
        if kind == "Temperature" and units in ("Cel", "C"):
            yield "redfish_temperature_celsius", labels, float(reading)
        elif kind == "Rotational" and units == "RPM":
            yield "redfish_fan_speed_rpm", labels, float(reading)
        elif kind == "Percent" and units == "%":
            yield "redfish_sensor_ratio", labels, float(reading) / 100.0
        elif kind == "Power" and units == "W":
            yield "redfish_sensor_power_watts", labels, float(reading)
        elif kind == "Voltage" and units == "V":
            yield "redfish_sensor_voltage_volts", labels, float(reading)


class RedfishCollector:
    """Prometheus collector mapped to the HPE Scale-up 3200 RMC payload."""

    def __init__(self, config: Config):
        self.config = config

    def collect(self):
        resource_labels = ["target", "chassis_id", "resource_type", "resource_id", "resource_name"]
        sensor_labels = ["target", "chassis_id", "sensor_id", "sensor_name", "physical_context"]
        families = {
            "redfish_resource_health": GaugeMetricFamily("redfish_resource_health", "Health: OK=1 Warning=.5 Critical=0 Unknown=-1", labels=resource_labels),
            "redfish_resource_power_state": GaugeMetricFamily("redfish_resource_power_state", "Power: On=1 transition=.5 Off=0 Unknown=-1", labels=["target", "chassis_id", "resource_type", "resource_id"]),
            "redfish_temperature_celsius": GaugeMetricFamily("redfish_temperature_celsius", "Temperature sensor reading", labels=sensor_labels),
            "redfish_fan_speed_rpm": GaugeMetricFamily("redfish_fan_speed_rpm", "Fan rotational speed", labels=sensor_labels),
            "redfish_sensor_ratio": GaugeMetricFamily("redfish_sensor_ratio", "Generic percent sensor as ratio 0..1", labels=sensor_labels),
            "redfish_sensor_power_watts": GaugeMetricFamily("redfish_sensor_power_watts", "Power reported by Sensor", labels=sensor_labels),
            "redfish_sensor_voltage_volts": GaugeMetricFamily("redfish_sensor_voltage_volts", "Voltage reported by Sensor", labels=sensor_labels),
            "redfish_chassis_power_input_watts": GaugeMetricFamily("redfish_chassis_power_input_watts", "Total chassis input power", labels=["target", "chassis_id"]),
            "redfish_power_supply_input_watts": GaugeMetricFamily("redfish_power_supply_input_watts", "Power supply input", labels=["target", "chassis_id", "power_supply_id", "power_supply_name"]),
            "redfish_power_supply_output_watts": GaugeMetricFamily("redfish_power_supply_output_watts", "Power supply output", labels=["target", "chassis_id", "power_supply_id", "power_supply_name"]),
            "redfish_system_processor_count": GaugeMetricFamily("redfish_system_processor_count", "Physical processor count", labels=["target", "system_id"]),
            "redfish_system_logical_processor_count": GaugeMetricFamily("redfish_system_logical_processor_count", "Logical processor count", labels=["target", "system_id"]),
            "redfish_system_memory_bytes": GaugeMetricFamily("redfish_system_memory_bytes", "System total memory", labels=["target", "system_id"]),
            "redfish_system_info": InfoMetricFamily("redfish_system", "ComputerSystem inventory", labels=["target", "system_id"]),
            "redfish_chassis_info": InfoMetricFamily("redfish_chassis", "Chassis inventory", labels=["target", "chassis_id"]),
            "redfish_manager_info": InfoMetricFamily("redfish_manager", "Manager inventory", labels=["target", "manager_id"]),
        }
        up = GaugeMetricFamily("redfish_up", "Whether target scrape succeeded", labels=["target"])
        duration = GaugeMetricFamily("redfish_scrape_duration_seconds", "Target scrape duration", labels=["target"])
        with ThreadPoolExecutor(max_workers=min(self.config.workers, len(self.config.targets))) as pool:
            future_map = {pool.submit(self._scrape, t): (t, time.monotonic()) for t in self.config.targets}
            for future in as_completed(future_map):
                target, started = future_map[future]
                try:
                    for metric, labels, value in future.result():
                        families[metric].add_metric(labels, value)
                    up.add_metric([target.name], 1)
                except Exception as exc:
                    LOG.warning("scrape failed target=%s error=%s", target.name, exc)
                    up.add_metric([target.name], 0)
                duration.add_metric([target.name], time.monotonic() - started)
        yield from (*families.values(), up, duration)

    def _scrape(self, target: Target):
        """Discover via advertised links. Never branch on vendor/model names."""
        client = RedfishClient(target, self.config.scrape_timeout)
        root = client.get("/redfish/v1/")
        samples = []
        systems = client.members(_uri(root, "Systems")) if _uri(root, "Systems") else []
        chassis = client.members(_uri(root, "Chassis")) if _uri(root, "Chassis") else []
        managers = client.members(_uri(root, "Managers")) if _uri(root, "Managers") else []
        samples.extend(_status_samples(target.name, "system", systems))
        samples.extend(_status_samples(target.name, "manager", managers))
        samples.extend(_power_state_samples(target.name, "system", systems))

        for system in systems:
            sid = _text(system.get("Id", "unknown"))
            summary = system.get("ProcessorSummary") or {}
            samples += [
                ("redfish_system_processor_count", [target.name, sid], float(summary.get("Count", 0))),
                ("redfish_system_logical_processor_count", [target.name, sid], float(summary.get("LogicalProcessorCount", 0))),
                ("redfish_system_memory_bytes", [target.name, sid], float((system.get("MemorySummary") or {}).get("TotalSystemMemoryGiB", 0)) * 1024**3),
                ("redfish_system_info", [target.name, sid], {
                    "hostname": _text(system.get("HostName")),
                    "manufacturer": _text(system.get("Manufacturer")), "model": _text(system.get("Model")),
                    "serial_number": _text(system.get("SerialNumber")), "bios_version": _text(system.get("BiosVersion")),
                    "system_type": _text(system.get("SystemType")),
                }),
            ]
        for manager in managers:
            mid = _text(manager.get("Id", "unknown"))
            samples.append(("redfish_manager_info", [target.name, mid], {
                "manager_name": _text(manager.get("Name")), "manager_type": _text(manager.get("ManagerType")),
                "manufacturer": _text(manager.get("Manufacturer")), "model": _text(manager.get("Model")),
                "firmware_version": _text(manager.get("FirmwareVersion")),
            }))

        for ch in chassis:
            cid = _text(ch.get("Id", "unknown"))
            samples.extend(_status_samples(target.name, "chassis", [ch], cid))
            samples.extend(_power_state_samples(target.name, "chassis", [ch], cid))
            samples.append(("redfish_chassis_info", [target.name, cid], {
                "chassis_name": _text(ch.get("Name")), "chassis_type": _text(ch.get("ChassisType")),
                "manufacturer": _text(ch.get("Manufacturer")), "model": _text(ch.get("Model")),
                "serial_number": _text(ch.get("SerialNumber")), "part_number": _text(ch.get("PartNumber")),
            }))
            chassis_power_watts = None
            thermal_uri = _uri(ch, "Thermal")
            if thermal_uri:
                thermal = client.get(thermal_uri)
                for sensor in thermal.get("Temperatures", []):
                    if sensor.get("ReadingCelsius") is not None:
                        sid = _text(sensor.get("MemberId", "unknown")); name = _text(sensor.get("Name", sid))
                        samples.append(("redfish_temperature_celsius", [target.name, cid, sid, name, _text(sensor.get("PhysicalContext"))], float(sensor["ReadingCelsius"])))
                for item in thermal.get("Fans", []):
                    if item.get("Reading") is not None and item.get("ReadingUnits") == "RPM":
                        fid = _text(item.get("MemberId", "unknown")); name = _text(item.get("Name", fid))
                        samples.append(("redfish_fan_speed_rpm", [target.name, cid, fid, name, _text(item.get("PhysicalContext"))], float(item["Reading"])))
            thermal_subsystem_uri = _uri(ch, "ThermalSubsystem")
            if thermal_subsystem_uri:
                subsystem = client.get(thermal_subsystem_uri)
                samples.extend(_status_samples(target.name, "thermal_subsystem", [subsystem], cid))
                fans_uri = _uri(subsystem, "Fans"); fans = client.members(fans_uri) if fans_uri else []
                samples.extend(_status_samples(target.name, "fan", fans, cid))
            sensors_uri = _uri(ch, "Sensors")
            samples.extend(_sensor_samples(target.name, cid, client.members(sensors_uri) if sensors_uri else []))
            power_uri = _uri(ch, "Power")
            if power_uri:
                controls = client.get(power_uri).get("PowerControl", [])
                if controls and controls[0].get("PowerConsumedWatts") is not None:
                    chassis_power_watts = float(controls[0]["PowerConsumedWatts"])
            power_subsystem_uri = _uri(ch, "PowerSubsystem")
            if power_subsystem_uri:
                subsystem = client.get(power_subsystem_uri)
                samples.extend(_status_samples(target.name, "power_subsystem", [subsystem], cid))
                supplies_uri = _uri(subsystem, "PowerSupplies"); supplies = client.members(supplies_uri) if supplies_uri else []
                samples.extend(_status_samples(target.name, "power_supply", supplies, cid))
                total_input = 0.0; input_count = 0
                for supply in supplies:
                    psid = _text(supply.get("Id", "unknown")); psname = _text(supply.get("Name", psid))
                    metrics_uri = _uri(supply, "Metrics")
                    if not metrics_uri: continue
                    metrics = client.get(metrics_uri)
                    input_value = (metrics.get("InputPowerWatts") or {}).get("Reading")
                    output_value = (metrics.get("OutputPowerWatts") or {}).get("Reading")
                    if input_value is not None:
                        value = float(input_value); total_input += value; input_count += 1
                        samples.append(("redfish_power_supply_input_watts", [target.name, cid, psid, psname], value))
                    if output_value is not None:
                        samples.append(("redfish_power_supply_output_watts", [target.name, cid, psid, psname], float(output_value)))
                if chassis_power_watts is None and input_count: chassis_power_watts = total_input
            if chassis_power_watts is not None:
                samples.append(("redfish_chassis_power_input_watts", [target.name, cid], chassis_power_watts))
        return samples
