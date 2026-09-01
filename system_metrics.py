from __future__ import annotations

import json
import math
import re
import socket
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psutil


CPU_WARNING = 70.0
CPU_CRITICAL = 90.0
CPU_TEMP_WARNING = 70.0
CPU_TEMP_CRITICAL = 85.0
NVME_TEMP_WARNING = 60.0
NVME_TEMP_CRITICAL = 75.0
MEMORY_WARNING = 80.0
MEMORY_CRITICAL = 90.0
DISK_WARNING = 80.0
DISK_CRITICAL = 90.0
SERVICE_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")
SERVICE_STATES = {"active", "inactive", "failed", "activating", "deactivating", "unknown"}
JST = ZoneInfo("Asia/Tokyo")
POWER_SAMPLE_PATH = Path("/run/n5105-dashboard/power.json")
POWER_ERROR = "CPU package power unavailable"


def unavailable_power() -> dict:
    return {
        "package_watts": None,
        "sample_seconds": None,
        "status": "unavailable",
        "estimated": True,
    }


def read_power_sample(
    path: Path = POWER_SAMPLE_PATH,
    now: datetime | None = None,
) -> tuple[dict, list[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("invalid power sample")
        version = raw.get("version")
        if isinstance(version, bool) or version != 1:
            raise ValueError("invalid power sample version")
        if raw.get("source") != "intel_rapl:package-0":
            raise ValueError("invalid power source")
        watts_raw = raw.get("package_watts")
        seconds_raw = raw.get("sample_seconds")
        if isinstance(watts_raw, bool) or isinstance(seconds_raw, bool):
            raise ValueError("invalid power values")
        watts = finite_float(watts_raw)
        seconds = finite_float(seconds_raw)
        if watts < 0 or seconds <= 0:
            raise ValueError("invalid power values")
        timestamp_raw = raw.get("sampled_at")
        if not isinstance(timestamp_raw, str) or not timestamp_raw.endswith("Z"):
            raise ValueError("invalid sample timestamp")
        sampled_at = datetime.fromisoformat(timestamp_raw[:-1] + "+00:00")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age_seconds = (
            current.astimezone(timezone.utc) - sampled_at.astimezone(timezone.utc)
        ).total_seconds()
        if age_seconds > 15 or age_seconds < -5:
            raise ValueError("power sample outside freshness window")
        return {
            "package_watts": watts,
            "sample_seconds": seconds,
            "status": "ok",
            "estimated": True,
        }, []
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        OverflowError,
    ):
        return unavailable_power(), [POWER_ERROR]


def classify(value: float, warning: float, critical: float) -> str:
    if value >= critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "ok"


def collect_temperatures(
    hwmon_root: Path = Path("/sys/class/hwmon"),
) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    errors: list[str] = []

    for sensor_dir in sorted(hwmon_root.glob("hwmon*")):
        try:
            chip = (sensor_dir / "name").read_text(encoding="utf-8").strip()
        except OSError:
            continue

        if chip not in {"coretemp", "nvme"}:
            continue

        for input_path in sorted(sensor_dir.glob("temp*_input")):
            suffix = input_path.stem[len("temp") : -len("_input")]
            label_path = sensor_dir / f"temp{suffix}_label"
            try:
                label = label_path.read_text(encoding="utf-8").strip()
            except OSError:
                continue

            if chip == "coretemp" and label == "Package id 0":
                display_name = "CPU Package"
                warning, critical = CPU_TEMP_WARNING, CPU_TEMP_CRITICAL
            elif chip == "coretemp" and label.startswith("Core "):
                display_name = label
                warning, critical = CPU_TEMP_WARNING, CPU_TEMP_CRITICAL
            elif chip == "nvme" and label == "Composite":
                display_name = "NVMe"
                warning, critical = NVME_TEMP_WARNING, NVME_TEMP_CRITICAL
            else:
                continue

            try:
                celsius = round(finite_float(input_path.read_text(encoding="utf-8").strip()) / 1000, 1)
                status = classify(celsius, warning, critical)
            except (OSError, ValueError):
                celsius = None
                status = "unavailable"
                errors.append(f"temperature sensor unavailable: {display_name}")

            items.append({"name": display_name, "celsius": celsius, "status": status})

    order = {"CPU Package": 0, "Core 0": 1, "Core 1": 2, "Core 2": 3, "Core 3": 4, "NVMe": 5}
    items.sort(key=lambda item: order.get(item["name"], 99))
    return items, errors


def load_service_config(path: Path) -> tuple[list[dict], list[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return [], ["service configuration unavailable"]
    if not isinstance(raw, list):
        return [], ["service configuration unavailable"]
    targets: list[dict] = []
    errors: list[str] = []
    for entry in raw:
        name = entry.get("name") if isinstance(entry, dict) else None
        unit = entry.get("unit") if isinstance(entry, dict) else None
        if (not isinstance(name, str) or not name.strip() or
                not isinstance(unit, str) or not SERVICE_UNIT_PATTERN.fullmatch(unit)):
            safe_name = name if isinstance(name, str) and name.strip() else "unnamed"
            errors.append(f"invalid service configuration entry: {safe_name}")
            continue
        targets.append({"name": name.strip(), "unit": unit})
    return targets, errors


def collect_services(
    targets: list[dict], runner: Callable = subprocess.run,
) -> tuple[list[dict], list[str]]:
    services: list[dict] = []
    errors: list[str] = []
    for target in targets:
        try:
            result = runner(
                ["systemctl", "is-active", "--", target["unit"]],
                capture_output=True, text=True, timeout=2, shell=False, check=False,
            )
            state = result.stdout.strip()
            if (
                not state
                or state not in SERVICE_STATES
                or (state == "active" and result.returncode != 0)
            ):
                state, status = "unknown", "unavailable"
                errors.append(f"service state unavailable: {target['name']}")
            else:
                status = "ok" if state == "active" else "critical"
        except (OSError, subprocess.SubprocessError):
            state, status = "unknown", "unavailable"
            errors.append(f"service state unavailable: {target['name']}")
        services.append({**target, "state": state, "status": status})
    return services, errors


def read_os_name(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except (OSError, UnicodeError):
        pass
    return "Unavailable"


def overall_status(items: list[dict], errors: list[str]) -> str:
    statuses = {item.get("status") for item in items}
    if "critical" in statuses:
        return "critical"
    if "warning" in statuses or "unavailable" in statuses or errors:
        return "warning"
    return "ok"


def finite_float(value) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite metric")
    return number


def finite_int(value) -> int:
    return int(finite_float(value))


def collect_status(
    *,
    psutil_module=psutil,
    hwmon_root: Path = Path("/sys/class/hwmon"),
    os_release_path: Path = Path("/etc/os-release"),
    service_config_path: Path = Path(__file__).parent / "config" / "services.json",
    service_targets: list[dict] | None = None,
    service_config_errors: list[str] | None = None,
    runner: Callable = subprocess.run,
    now: datetime | None = None,
    hostname: str | None = None,
    power_path: Path = POWER_SAMPLE_PATH,
) -> dict:
    if now is None:
        collected_at = datetime.now(JST)
    elif now.tzinfo is None:
        collected_at = now.replace(tzinfo=JST)
    else:
        collected_at = now.astimezone(JST)
    errors: list[str] = []
    informational_errors: list[str] = []

    try:
        cpu_usage = round(finite_float(psutil_module.cpu_percent(interval=0.1)), 1)
        cpu = {
            "usage_percent": cpu_usage,
            "status": classify(cpu_usage, CPU_WARNING, CPU_CRITICAL),
            "load": None,
            "frequency_mhz": None,
            "logical_cpus": None,
        }
    except (AttributeError, OSError, TypeError, ValueError):
        cpu = {
            "usage_percent": None,
            "status": "unavailable",
            "load": None,
            "frequency_mhz": None,
            "logical_cpus": None,
        }
        errors.append("CPU utilization unavailable")

    try:
        load_1, load_5, load_15 = psutil_module.getloadavg()
        cpu["load"] = {
            "1m": round(finite_float(load_1), 2),
            "5m": round(finite_float(load_5), 2),
            "15m": round(finite_float(load_15), 2),
        }
    except (AttributeError, OSError, TypeError, ValueError):
        error = "CPU load averages unavailable"
        errors.append(error)
        informational_errors.append(error)

    try:
        frequency = psutil_module.cpu_freq()
        cpu["frequency_mhz"] = (
            round(finite_float(frequency.current), 0) if frequency else None
        )
    except (AttributeError, OSError, TypeError, ValueError):
        error = "CPU frequency unavailable"
        errors.append(error)
        informational_errors.append(error)

    try:
        cpu["logical_cpus"] = finite_int(psutil_module.cpu_count(logical=True))
    except (AttributeError, OSError, TypeError, ValueError):
        error = "logical CPU count unavailable"
        errors.append(error)
        informational_errors.append(error)

    def usage_payload(values, warning, critical):
        percent = round(finite_float(values.percent), 1)
        return {
            "total_bytes": finite_int(values.total),
            "used_bytes": finite_int(values.used),
            "usage_percent": percent,
            "status": classify(percent, warning, critical),
        }

    try:
        memory = usage_payload(psutil_module.virtual_memory(), MEMORY_WARNING, MEMORY_CRITICAL)
    except (AttributeError, OSError, TypeError, ValueError):
        memory = {"total_bytes": None, "used_bytes": None, "usage_percent": None, "status": "unavailable"}
        errors.append("memory metrics unavailable")

    try:
        swap_values = psutil_module.swap_memory()
        swap = {
            "total_bytes": finite_int(swap_values.total),
            "used_bytes": finite_int(swap_values.used),
            "usage_percent": round(finite_float(swap_values.percent), 1),
            "status": "ok",
        }
    except (AttributeError, OSError, TypeError, ValueError):
        swap = {"total_bytes": None, "used_bytes": None, "usage_percent": None, "status": "unavailable"}
        errors.append("swap metrics unavailable")

    try:
        disk = usage_payload(psutil_module.disk_usage("/"), DISK_WARNING, DISK_CRITICAL)
    except (AttributeError, OSError, TypeError, ValueError):
        disk = {"total_bytes": None, "used_bytes": None, "usage_percent": None, "status": "unavailable"}
        errors.append("disk metrics unavailable")

    try:
        uptime_seconds = max(0, int(collected_at.timestamp() - finite_float(psutil_module.boot_time())))
    except (AttributeError, OSError, TypeError, ValueError):
        uptime_seconds = None
        errors.append("uptime unavailable")

    temperatures, temperature_errors = collect_temperatures(hwmon_root)
    if service_targets is None:
        targets, config_errors = load_service_config(service_config_path)
    else:
        targets = service_targets
        config_errors = list(service_config_errors or [])
    services, service_errors = collect_services(targets, runner=runner)
    errors.extend(temperature_errors + config_errors + service_errors)

    os_name = read_os_name(os_release_path)
    if os_name == "Unavailable":
        errors.append("host OS unavailable")

    power, power_errors = read_power_sample(power_path, now=collected_at)
    errors.extend(power_errors)
    informational_errors.extend(power_errors)

    core_items = [cpu, memory, disk]
    health_items = [*core_items, *temperatures, *services]
    health = (
        "critical"
        if all(item["status"] == "unavailable" for item in core_items)
        else overall_status(
            health_items,
            [
                error
                for error in errors
                if error != "swap metrics unavailable"
                and error not in informational_errors
            ],
        )
    )
    return {
        "collected_at": collected_at.isoformat(timespec="seconds"),
        "overall_status": health,
        "host": {
            "hostname": hostname or socket.gethostname(),
            "os": os_name,
            "uptime_seconds": uptime_seconds,
        },
        "cpu": cpu,
        "temperatures": temperatures,
        "memory": memory,
        "swap": swap,
        "disk": disk,
        "services": services,
        "power": power,
        "errors": errors,
    }
