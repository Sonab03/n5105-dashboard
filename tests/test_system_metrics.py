from datetime import datetime, timezone
import json
import math
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import subprocess

from system_metrics import (
    classify,
    collect_status,
    collect_temperatures,
    collect_services,
    load_service_config,
)


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


def test_load_service_config_rejects_invalid_utf8(tmp_path):
    config = tmp_path / "services.json"
    config.write_bytes(b"[\xff")
    assert load_service_config(config) == ([], ["service configuration unavailable"])


def test_collect_services_normalizes_unexpected_multiline_state():
    def runner(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="active\nsecret detail\n", stderr="")
    services, errors = collect_services(
        [{"name": "Rate", "unit": "unionpay-rate.service"}], runner=runner
    )
    assert services == [{"name": "Rate", "unit": "unionpay-rate.service", "state": "unknown", "status": "unavailable"}]
    assert errors == ["service state unavailable: Rate"]


class FakePsutil:
    @staticmethod
    def cpu_percent(interval):
        assert interval == 0.1
        return 25.0

    @staticmethod
    def getloadavg():
        return (0.1, 0.2, 0.3)

    @staticmethod
    def cpu_freq():
        return SimpleNamespace(current=1800.0)

    @staticmethod
    def cpu_count(logical=True):
        assert logical is True
        return 4

    @staticmethod
    def virtual_memory():
        return SimpleNamespace(total=16_000, used=4_000, percent=25.0)

    @staticmethod
    def swap_memory():
        return SimpleNamespace(total=4_000, used=0, percent=0.0)

    @staticmethod
    def disk_usage(path):
        assert path == "/"
        return SimpleNamespace(total=100_000, used=20_000, percent=20.0)

    @staticmethod
    def boot_time():
        return 1_700_000_000.0


def test_collect_status_assembles_stable_public_shape(tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text('PRETTY_NAME="Ubuntu 24.04.2 LTS"\n', encoding="utf-8")
    services = tmp_path / "services.json"
    services.write_text("[]", encoding="utf-8")
    now = datetime(2026, 9, 1, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    payload = collect_status(
        psutil_module=FakePsutil,
        hwmon_root=tmp_path / "empty-hwmon",
        os_release_path=os_release,
        service_config_path=services,
        now=now,
        hostname="ubuntu-n5105",
    )

    assert set(payload) == {
        "collected_at", "overall_status", "host", "cpu", "temperatures",
        "memory", "swap", "disk", "services", "errors",
    }
    assert set(payload["host"]) == {"hostname", "os", "uptime_seconds"}
    assert set(payload["cpu"]) == {
        "usage_percent", "status", "load", "frequency_mhz", "logical_cpus",
    }
    assert set(payload["memory"]) == {
        "total_bytes", "used_bytes", "usage_percent", "status",
    }
    assert set(payload["swap"]) == {
        "total_bytes", "used_bytes", "usage_percent", "status",
    }
    assert set(payload["disk"]) == {
        "total_bytes", "used_bytes", "usage_percent", "status",
    }
    assert payload["collected_at"] == "2026-09-01T00:00:00+09:00"
    assert payload["overall_status"] == "ok"
    assert payload["host"]["hostname"] == "ubuntu-n5105"
    assert payload["host"]["os"] == "Ubuntu 24.04.2 LTS"
    assert payload["cpu"] == {
        "usage_percent": 25.0,
        "status": "ok",
        "load": {"1m": 0.1, "5m": 0.2, "15m": 0.3},
        "frequency_mhz": 1800.0,
        "logical_cpus": 4,
    }
    assert payload["memory"]["used_bytes"] == 4_000
    assert payload["disk"]["usage_percent"] == 20.0
    assert payload["services"] == []
    assert payload["errors"] == []


def status_with(psutil_module, tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text('PRETTY_NAME="Ubuntu 24.04.2 LTS"\n', encoding="utf-8")
    services = tmp_path / "services.json"
    services.write_text("[]", encoding="utf-8")
    return collect_status(
        psutil_module=psutil_module,
        hwmon_root=tmp_path / "empty-hwmon",
        os_release_path=os_release,
        service_config_path=services,
        now=datetime(2026, 9, 1, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        hostname="ubuntu-n5105",
    )


def assert_only_finite_floats(value):
    if isinstance(value, dict):
        for nested in value.values():
            assert_only_finite_floats(nested)
    elif isinstance(value, list):
        for nested in value:
            assert_only_finite_floats(nested)
    elif isinstance(value, float):
        assert math.isfinite(value)


def test_collect_status_keeps_other_metrics_when_memory_fails(tmp_path):
    class MemoryFailingPsutil(FakePsutil):
        @staticmethod
        def virtual_memory():
            raise OSError("private detail")

    payload = status_with(MemoryFailingPsutil, tmp_path)

    assert payload["memory"]["status"] == "unavailable"
    assert payload["cpu"]["usage_percent"] == 25.0
    assert payload["overall_status"] == "warning"
    assert payload["errors"] == ["memory metrics unavailable"]


def test_collect_status_is_critical_when_all_core_collectors_fail(tmp_path):
    class CoreFailingPsutil(FakePsutil):
        @staticmethod
        def cpu_percent(interval):
            raise OSError("private CPU detail")

        @staticmethod
        def virtual_memory():
            raise OSError("private memory detail")

        @staticmethod
        def disk_usage(path):
            raise OSError("private disk detail")

    payload = status_with(CoreFailingPsutil, tmp_path)

    assert payload["cpu"]["status"] == "unavailable"
    assert payload["memory"]["status"] == "unavailable"
    assert payload["disk"]["status"] == "unavailable"
    assert payload["overall_status"] == "critical"
    assert all("private" not in error for error in payload["errors"])


def test_collect_status_keeps_overall_ok_when_only_swap_fails(tmp_path):
    class SwapFailingPsutil(FakePsutil):
        @staticmethod
        def swap_memory():
            raise OSError("private swap detail")

    payload = status_with(SwapFailingPsutil, tmp_path)

    assert payload["swap"] == {
        "total_bytes": None,
        "used_bytes": None,
        "usage_percent": None,
        "status": "unavailable",
    }
    assert payload["overall_status"] == "ok"
    assert payload["errors"] == ["swap metrics unavailable"]


def test_collect_status_sanitizes_non_finite_psutil_metrics_for_strict_json(tmp_path):
    class NonFinitePsutil(FakePsutil):
        @staticmethod
        def cpu_percent(interval):
            return float("nan")

        @staticmethod
        def virtual_memory():
            return SimpleNamespace(total=16_000, used=4_000, percent=float("inf"))

        @staticmethod
        def swap_memory():
            return SimpleNamespace(total=4_000, used=0, percent=float("nan"))

        @staticmethod
        def disk_usage(path):
            return SimpleNamespace(total=100_000, used=20_000, percent=float("-inf"))

    payload = status_with(NonFinitePsutil, tmp_path)

    assert payload["cpu"]["status"] == "unavailable"
    assert payload["memory"]["status"] == "unavailable"
    assert payload["swap"]["status"] == "unavailable"
    assert payload["disk"]["status"] == "unavailable"
    assert_only_finite_floats(payload)
    json.dumps(payload, allow_nan=False)
    assert all("private" not in error for error in payload["errors"])


def test_collect_status_isolates_invalid_os_release_encoding(tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_bytes(b"PRETTY_NAME=\xff")
    services = tmp_path / "services.json"
    services.write_text("[]", encoding="utf-8")

    payload = collect_status(
        psutil_module=FakePsutil,
        hwmon_root=tmp_path / "empty-hwmon",
        os_release_path=os_release,
        service_config_path=services,
        now=datetime(2026, 9, 1, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        hostname="ubuntu-n5105",
    )

    assert payload["host"] == {
        "hostname": "ubuntu-n5105",
        "os": "Unavailable",
        "uptime_seconds": 88_188_400,
    }
    assert payload["cpu"]["usage_percent"] == 25.0
    assert payload["overall_status"] == "warning"
    assert payload["errors"] == ["host OS unavailable"]


def test_collect_status_normalizes_injected_utc_timestamp_to_jst(tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text('PRETTY_NAME="Ubuntu 24.04.2 LTS"\n', encoding="utf-8")
    services = tmp_path / "services.json"
    services.write_text("[]", encoding="utf-8")

    payload = collect_status(
        psutil_module=FakePsutil,
        hwmon_root=tmp_path / "empty-hwmon",
        os_release_path=os_release,
        service_config_path=services,
        now=datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc),
        hostname="ubuntu-n5105",
    )

    assert payload["collected_at"] == "2026-09-01T00:00:00+09:00"
