from pathlib import Path
import json
import subprocess

from system_metrics import classify, collect_temperatures, collect_services, load_service_config


def write_sensor(root: Path, hwmon: str, chip: str, index: int, label: str, value: int):
    sensor = root / hwmon
    sensor.mkdir(parents=True, exist_ok=True)
    (sensor / "name").write_text(chip, encoding="utf-8")
    (sensor / f"temp{index}_label").write_text(label, encoding="utf-8")
    (sensor / f"temp{index}_input").write_text(str(value), encoding="utf-8")


def test_classify_uses_inclusive_warning_and_critical_boundaries():
    assert classify(69.9, 70, 90) == "ok"
    assert classify(70, 70, 90) == "warning"
    assert classify(90, 70, 90) == "critical"


def test_collect_temperatures_normalizes_cpu_and_nvme_labels(tmp_path):
    write_sensor(tmp_path, "hwmon0", "coretemp", 1, "Package id 0", 52000)
    write_sensor(tmp_path, "hwmon0", "coretemp", 2, "Core 0", 53000)
    write_sensor(tmp_path, "hwmon1", "nvme", 1, "Composite", 44850)

    temperatures, errors = collect_temperatures(tmp_path)

    assert errors == []
    assert temperatures == [
        {"name": "CPU Package", "celsius": 52.0, "status": "ok"},
        {"name": "Core 0", "celsius": 53.0, "status": "ok"},
        {"name": "NVMe", "celsius": 44.9, "status": "ok"},
    ]


def test_collect_temperatures_reports_bad_sensor_without_leaking_path(tmp_path):
    write_sensor(tmp_path, "hwmon0", "coretemp", 1, "Package id 0", 52000)
    (tmp_path / "hwmon0" / "temp1_input").write_text("bad", encoding="utf-8")

    temperatures, errors = collect_temperatures(tmp_path)

    assert temperatures == [
        {"name": "CPU Package", "celsius": None, "status": "unavailable"}
    ]
    assert errors == ["temperature sensor unavailable: CPU Package"]
    assert str(tmp_path) not in errors[0]


def test_load_service_config_accepts_service_units_and_rejects_unsafe_values(tmp_path):
    config = tmp_path / "services.json"
    config.write_text(json.dumps([
        {"name": "Rate", "unit": "unionpay-rate.service"},
        {"name": "Unsafe", "unit": "x.service;reboot"},
    ]), encoding="utf-8")
    targets, errors = load_service_config(config)
    assert targets == [{"name": "Rate", "unit": "unionpay-rate.service"}]
    assert errors == ["invalid service configuration entry: Unsafe"]


def test_collect_services_uses_argument_list_and_marks_inactive_critical():
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 3, stdout="inactive\n", stderr="")
    services, errors = collect_services(
        [{"name": "Rate", "unit": "unionpay-rate.service"}], runner=runner
    )
    assert calls[0][0] == ["systemctl", "is-active", "--", "unionpay-rate.service"]
    assert calls[0][1]["shell"] is False
    assert services == [{"name": "Rate", "unit": "unionpay-rate.service", "state": "inactive", "status": "critical"}]
    assert errors == []


def test_collect_services_sanitizes_query_failure():
    def runner(args, **kwargs):
        raise OSError("private local detail")
    services, errors = collect_services(
        [{"name": "Rate", "unit": "unionpay-rate.service"}], runner=runner
    )
    assert services[0]["state"] == "unknown"
    assert services[0]["status"] == "unavailable"
    assert errors == ["service state unavailable: Rate"]
