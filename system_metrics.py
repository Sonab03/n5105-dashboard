from __future__ import annotations

from pathlib import Path


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
                celsius = round(float(input_path.read_text(encoding="utf-8").strip()) / 1000, 1)
                status = classify(celsius, warning, critical)
            except (OSError, ValueError):
                celsius = None
                status = "unavailable"
                errors.append(f"temperature sensor unavailable: {display_name}")

            items.append({"name": display_name, "celsius": celsius, "status": status})

    order = {"CPU Package": 0, "Core 0": 1, "Core 1": 2, "Core 2": 3, "Core 3": 4, "NVMe": 5}
    items.sort(key=lambda item: order.get(item["name"], 99))
    return items, errors
