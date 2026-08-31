# N5105 Status Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy a read-only, Cloudflare Access-protected status dashboard for the N5105 at `dashboard.sonab.uk`.

**Architecture:** A new FastAPI service in `/home/ubuntu/projects/n5105-dashboard` collects current metrics through `psutil`, Linux hwmon/sysfs, and fixed `systemctl is-active` calls. A responsive page polls `/api/status` every 10 seconds; Uvicorn listens only on `127.0.0.1:8001`, with the existing Cloudflare Tunnel and Cloudflare Access providing external routing and authentication.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Jinja2, psutil, pytest, HTTPX, systemd, Cloudflare Tunnel, Cloudflare Access

**Spec:** `docs/superpowers/specs/2026-08-31-n5105-status-dashboard-design.md`

## Global Constraints

- Work only in `/home/ubuntu/projects/n5105-dashboard`; do not modify `/home/ubuntu/projects/unionpay-rate`.
- The dashboard is read-only and accepts no command, path, unit, or collection target from an HTTP request.
- Bind the service only to `127.0.0.1:8001`.
- Refresh current status every 10 seconds; do not store metric history.
- Protect `dashboard.sonab.uk/*` with Cloudflare Access One-Time PIN, an allow-only email policy, and a 24-hour session.
- Do not expose source paths, raw command output, process lists, network addresses, secrets, Access emails, or Cloudflare credentials.
- Use test-driven development for every behavior change and commit only after the full test suite passes.
- Use Python 3.12.3 already installed on the N5105.
- Use Tunnel ID `3eaec435-410f-461c-958b-fdccb6c092e7` for the `dashboard.sonab.uk` DNS route.

---

## File Map

- `system_metrics.py`: thresholds, system collectors, temperature parsing, service configuration, service-state checks, and final payload assembly.
- `app.py`: FastAPI application factory and the `/`, `/api/status`, and `/healthz` routes.
- `config/services.json`: extensible display names and fixed systemd units.
- `templates/dashboard.html`: dark responsive interface, rendering, and 10-second polling.
- `tests/test_system_metrics.py`: collector, threshold, service, aggregation, and failure tests.
- `tests/test_app.py`: route and response-header tests.
- `tests/test_deployment.py`: service-unit security and loopback-binding assertions.
- `deploy/n5105-dashboard.service`: checked-in systemd unit installed during deployment.
- `requirements.txt`: runtime and test dependencies.
- `.gitignore`: generated Python and virtual-environment files.
- `README.md`: local development, service configuration, deployment, and verification commands.

---

### Task 1: Thresholds and Temperature Collection

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `system_metrics.py`
- Create: `tests/test_system_metrics.py`

**Interfaces:**
- Produces: `classify(value: float, warning: float, critical: float) -> str`
- Produces: `collect_temperatures(hwmon_root: Path = Path("/sys/class/hwmon")) -> tuple[list[dict], list[str]]`
- Temperature items: `{"name": str, "celsius": float | None, "status": "ok" | "warning" | "critical" | "unavailable"}`

- [ ] **Step 1: Add dependencies and create the isolated environment**

Create `requirements.txt`:

```text
fastapi>=0.115,<1
httpx>=0.28,<1
jinja2>=3.1,<4
psutil>=7,<8
pytest>=8,<9
uvicorn>=0.34,<1
```

Create `.gitignore`:

```text
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

Run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Expected: dependency installation exits 0 and `.venv/bin/pytest --version` prints a pytest version.

- [ ] **Step 2: Write failing threshold and temperature tests**

Create `tests/test_system_metrics.py` with these initial tests:

```python
from pathlib import Path

from system_metrics import classify, collect_temperatures


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
```

- [ ] **Step 3: Run the tests and verify the intended failure**

Run:

```bash
.venv/bin/pytest tests/test_system_metrics.py -v
```

Expected: collection fails because `system_metrics` or the named functions do not exist.

- [ ] **Step 4: Implement the minimal threshold and temperature collector**

Create `system_metrics.py` with:

```python
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
            suffix = input_path.stem.removeprefix("temp").removesuffix("_input")
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
```

- [ ] **Step 5: Run the focused and full tests**

Run:

```bash
.venv/bin/pytest tests/test_system_metrics.py -v
.venv/bin/pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the collector**

```bash
git add requirements.txt .gitignore system_metrics.py tests/test_system_metrics.py
git commit -m "feat: add metric thresholds and temperature collection"
```

---

### Task 2: Extensible systemd Service Monitoring

**Files:**
- Modify: `system_metrics.py`
- Modify: `tests/test_system_metrics.py`
- Create: `config/services.json`

**Interfaces:**
- Produces: `load_service_config(path: Path) -> tuple[list[dict], list[str]]`
- Produces: `collect_services(targets: list[dict], runner: Callable = subprocess.run) -> tuple[list[dict], list[str]]`
- Service items: `{"name": str, "unit": str, "state": str, "status": str}`

- [ ] **Step 1: Write failing configuration and service-state tests**

Append to `tests/test_system_metrics.py`:

```python
import json
import subprocess

from system_metrics import collect_services, load_service_config


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
    assert services == [{
        "name": "Rate",
        "unit": "unionpay-rate.service",
        "state": "inactive",
        "status": "critical",
    }]
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
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
.venv/bin/pytest tests/test_system_metrics.py -k "service" -v
```

Expected: tests fail because the two service functions do not exist.

- [ ] **Step 3: Implement configuration validation and fixed service checks**

Add these imports and functions to `system_metrics.py`:

```python
import json
import re
import subprocess
from collections.abc import Callable


SERVICE_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")


def load_service_config(path: Path) -> tuple[list[dict], list[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return [], ["service configuration unavailable"]

    if not isinstance(raw, list):
        return [], ["service configuration unavailable"]

    targets: list[dict] = []
    errors: list[str] = []
    for entry in raw:
        name = entry.get("name") if isinstance(entry, dict) else None
        unit = entry.get("unit") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name.strip() or not isinstance(unit, str) or not SERVICE_UNIT_PATTERN.fullmatch(unit):
            safe_name = name if isinstance(name, str) and name.strip() else "unnamed"
            errors.append(f"invalid service configuration entry: {safe_name}")
            continue
        targets.append({"name": name.strip(), "unit": unit})
    return targets, errors


def collect_services(
    targets: list[dict],
    runner: Callable = subprocess.run,
) -> tuple[list[dict], list[str]]:
    services: list[dict] = []
    errors: list[str] = []
    for target in targets:
        try:
            result = runner(
                ["systemctl", "is-active", "--", target["unit"]],
                capture_output=True,
                text=True,
                timeout=2,
                shell=False,
                check=False,
            )
            state = result.stdout.strip() or "unknown"
            status = "ok" if state == "active" else "critical"
        except (OSError, subprocess.SubprocessError):
            state = "unknown"
            status = "unavailable"
            errors.append(f"service state unavailable: {target['name']}")
        services.append({**target, "state": state, "status": status})
    return services, errors
```

Create `config/services.json`:

```json
[
  {
    "name": "UnionPay Rate",
    "unit": "unionpay-rate.service"
  },
  {
    "name": "Cloudflare Tunnel",
    "unit": "cloudflared.service"
  }
]
```

- [ ] **Step 4: Run tests and commit**

```bash
.venv/bin/pytest -q
git add system_metrics.py tests/test_system_metrics.py config/services.json
git commit -m "feat: add configurable service monitoring"
```

Expected: all tests pass before the commit is created.

---

### Task 3: Assemble the Status Payload

**Files:**
- Modify: `system_metrics.py`
- Modify: `tests/test_system_metrics.py`

**Interfaces:**
- Produces: `collect_status(*, psutil_module=psutil, hwmon_root: Path = Path("/sys/class/hwmon"), os_release_path: Path = Path("/etc/os-release"), service_config_path: Path = Path(__file__).parent / "config" / "services.json", runner=subprocess.run, now: datetime | None = None, hostname: str | None = None) -> dict`
- Payload fields exactly match the top-level JSON shape in the approved spec.

- [ ] **Step 1: Write a failing deterministic aggregation test**

Append a fake psutil provider and test to `tests/test_system_metrics.py`:

```python
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from system_metrics import collect_status


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
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
.venv/bin/pytest tests/test_system_metrics.py::test_collect_status_assembles_stable_public_shape -v
```

Expected: failure because `collect_status` does not exist.

- [ ] **Step 3: Implement payload collection and overall-health precedence**

Add to `system_metrics.py`:

```python
import socket
from datetime import datetime
from zoneinfo import ZoneInfo

import psutil


JST = ZoneInfo("Asia/Tokyo")


def read_os_name(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "Unavailable"


def overall_status(items: list[dict], errors: list[str]) -> str:
    statuses = {item.get("status") for item in items}
    if "critical" in statuses:
        return "critical"
    if "warning" in statuses or "unavailable" in statuses or errors:
        return "warning"
    return "ok"


def collect_status(
    *,
    psutil_module=psutil,
    hwmon_root: Path = Path("/sys/class/hwmon"),
    os_release_path: Path = Path("/etc/os-release"),
    service_config_path: Path = Path(__file__).parent / "config" / "services.json",
    runner=subprocess.run,
    now: datetime | None = None,
    hostname: str | None = None,
) -> dict:
    collected_at = now or datetime.now(JST)
    errors: list[str] = []

    try:
        cpu_usage = round(float(psutil_module.cpu_percent(interval=0.1)), 1)
        load_1, load_5, load_15 = psutil_module.getloadavg()
        frequency = psutil_module.cpu_freq()
        cpu = {
            "usage_percent": cpu_usage,
            "status": classify(cpu_usage, CPU_WARNING, CPU_CRITICAL),
            "load": {"1m": round(load_1, 2), "5m": round(load_5, 2), "15m": round(load_15, 2)},
            "frequency_mhz": round(float(frequency.current), 0) if frequency else None,
            "logical_cpus": psutil_module.cpu_count(logical=True),
        }
    except (AttributeError, OSError, TypeError, ValueError):
        cpu = {"usage_percent": None, "status": "unavailable", "load": None, "frequency_mhz": None, "logical_cpus": None}
        errors.append("CPU metrics unavailable")

    def usage_payload(values, warning, critical):
        percent = round(float(values.percent), 1)
        return {
            "total_bytes": int(values.total),
            "used_bytes": int(values.used),
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
            "total_bytes": int(swap_values.total),
            "used_bytes": int(swap_values.used),
            "usage_percent": round(float(swap_values.percent), 1),
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
        uptime_seconds = max(0, int(collected_at.timestamp() - psutil_module.boot_time()))
    except (AttributeError, OSError, TypeError, ValueError):
        uptime_seconds = None
        errors.append("uptime unavailable")

    temperatures, temperature_errors = collect_temperatures(hwmon_root)
    targets, config_errors = load_service_config(service_config_path)
    services, service_errors = collect_services(targets, runner=runner)
    errors.extend(temperature_errors + config_errors + service_errors)

    health_items = [cpu, memory, disk, *temperatures, *services]
    return {
        "collected_at": collected_at.isoformat(timespec="seconds"),
        "overall_status": overall_status(health_items, errors),
        "host": {
            "hostname": hostname or socket.gethostname(),
            "os": read_os_name(os_release_path),
            "uptime_seconds": uptime_seconds,
        },
        "cpu": cpu,
        "temperatures": temperatures,
        "memory": memory,
        "swap": swap,
        "disk": disk,
        "services": services,
        "errors": errors,
    }
```

- [ ] **Step 4: Add and pass partial- and total-failure tests**

Append these exact tests to `tests/test_system_metrics.py`:

```python
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
```

Run:

```bash
.venv/bin/pytest tests/test_system_metrics.py::test_collect_status_is_critical_when_all_core_collectors_fail -v
```

Expected: FAIL because the basic aggregation reports `warning` when all three core collectors are unavailable.

- [ ] **Step 5: Add the minimal all-core failure override**

Immediately before the return value in `collect_status`, replace the original
`health_items` assignment with:

```python
    core_items = [cpu, memory, disk]
    health_items = [*core_items, *temperatures, *services]
    health = (
        "critical"
        if all(item["status"] == "unavailable" for item in core_items)
        else overall_status(health_items, errors)
    )
```

Change the returned field to:

```python
        "overall_status": health,
```

Run:

```bash
.venv/bin/pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the aggregate collector**

```bash
git add system_metrics.py tests/test_system_metrics.py
git commit -m "feat: assemble dashboard status payload"
```

---

### Task 4: FastAPI Routes

**Files:**
- Create: `app.py`
- Create: `templates/dashboard.html`
- Create: `tests/test_app.py`

**Interfaces:**
- Produces: `create_app(status_provider=collect_status, template_dir: Path | None = None) -> FastAPI`
- Produces: `app = create_app()`
- HTTP: `GET /`, `GET /api/status`, `GET /healthz`

- [ ] **Step 1: Write failing route tests**

Create `tests/test_app.py`:

```python
from fastapi.testclient import TestClient

from app import create_app


SAMPLE = {
    "collected_at": "2026-09-01T00:00:00+09:00",
    "overall_status": "ok",
    "host": {"hostname": "ubuntu-n5105", "os": "Ubuntu 24.04.2 LTS", "uptime_seconds": 3600},
    "cpu": {"usage_percent": 25.0, "status": "ok", "load": {"1m": 0.1, "5m": 0.2, "15m": 0.3}, "frequency_mhz": 1800, "logical_cpus": 4},
    "temperatures": [],
    "memory": {"total_bytes": 16000, "used_bytes": 4000, "usage_percent": 25.0, "status": "ok"},
    "swap": {"total_bytes": 4000, "used_bytes": 0, "usage_percent": 0.0, "status": "ok"},
    "disk": {"total_bytes": 100000, "used_bytes": 20000, "usage_percent": 20.0, "status": "ok"},
    "services": [],
    "errors": [],
}


def make_client():
    return TestClient(create_app(status_provider=lambda: SAMPLE))


def test_dashboard_route_returns_html():
    response = make_client().get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "N5105 Dashboard" in response.text


def test_status_route_returns_provider_payload_without_cache():
    response = make_client().get("/api/status")
    assert response.status_code == 200
    assert response.json() == SAMPLE
    assert response.headers["cache-control"] == "no-store"


def test_health_route_checks_only_dashboard_liveness():
    response = make_client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the route tests and verify failure**

```bash
.venv/bin/pytest tests/test_app.py -v
```

Expected: failure because `app.py` does not exist.

- [ ] **Step 3: Implement the application factory and minimal page shell**

Create `app.py`:

```python
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from system_metrics import collect_status


BASE_DIR = Path(__file__).parent


def create_app(
    status_provider: Callable[[], dict] = collect_status,
    template_dir: Path | None = None,
) -> FastAPI:
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(template_dir or BASE_DIR / "templates"))

    @application.get("/")
    def dashboard(request: Request):
        return templates.TemplateResponse(request=request, name="dashboard.html", context={})

    @application.get("/api/status")
    def api_status():
        return JSONResponse(status_provider(), headers={"Cache-Control": "no-store"})

    @application.get("/healthz")
    def healthz():
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    return application


app = create_app()
```

Create an initial `templates/dashboard.html`:

```html
<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>N5105 Dashboard</title></head>
<body><main><h1>N5105 Dashboard</h1><p id="connection">正在加载状态…</p></main></body>
</html>
```

- [ ] **Step 4: Run all tests and commit**

```bash
.venv/bin/pytest -q
git add app.py templates/dashboard.html tests/test_app.py
git commit -m "feat: expose dashboard routes"
```

Expected: all tests pass before commit.

---

### Task 5: Responsive Dashboard Interface

**Files:**
- Modify: `templates/dashboard.html`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: the complete `/api/status` payload from Task 3.
- Produces: DOM containers `summary`, `cpu`, `temperatures`, `memory`, `disk`, `services`, `connection`, and `updated`.
- Polling: immediate request followed by one request every 10,000 milliseconds.

- [ ] **Step 1: Write failing page-contract tests**

Extend `test_dashboard_route_returns_html` with:

```python
for element_id in ("summary", "cpu", "temperatures", "memory", "disk", "services", "connection", "updated"):
    assert f'id="{element_id}"' in response.text
assert 'setInterval(refresh, 10000)' in response.text
assert 'fetch("/api/status", {cache: "no-store"})' in response.text
assert "previousData" in response.text
```

Run:

```bash
.venv/bin/pytest tests/test_app.py::test_dashboard_route_returns_html -v
```

Expected: failure because the initial page shell lacks the dashboard contract.

- [ ] **Step 2: Implement the full template**

Replace `templates/dashboard.html` with a self-contained template that has:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>N5105 Dashboard</title>
  <style>
    :root { color-scheme: dark; --bg:#080b10; --card:#111722; --line:#263042; --text:#eef3fb; --muted:#91a0b5; --ok:#35d07f; --warning:#f3bd43; --critical:#ff5d68; --unavailable:#778398; }
    * { box-sizing:border-box; }
    body { margin:0; background:radial-gradient(circle at top,#162033 0,#080b10 42%); color:var(--text); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; min-height:100vh; }
    main { width:min(1120px,calc(100% - 32px)); margin:0 auto; padding:32px 0 56px; }
    header { display:flex; justify-content:space-between; gap:20px; align-items:flex-start; margin-bottom:20px; }
    h1 { margin:0; font-size:clamp(28px,5vw,46px); letter-spacing:-.04em; }
    h2 { margin:0 0 14px; font-size:17px; }
    p { margin:4px 0; color:var(--muted); }
    .grid { display:grid; grid-template-columns:repeat(12,1fr); gap:14px; }
    .card { grid-column:span 4; background:rgba(17,23,34,.92); border:1px solid var(--line); border-radius:18px; padding:18px; box-shadow:0 18px 50px rgba(0,0,0,.24); }
    .wide { grid-column:span 6; }
    .full { grid-column:1/-1; }
    .metric { display:flex; justify-content:space-between; gap:16px; padding:9px 0; border-top:1px solid rgba(38,48,66,.65); }
    .metric:first-of-type { border-top:0; }
    .value { color:var(--text); font-variant-numeric:tabular-nums; }
    .badge { display:inline-flex; align-items:center; gap:7px; padding:6px 10px; border-radius:999px; background:#1a2330; color:var(--muted); font-size:13px; }
    .dot { width:8px; height:8px; border-radius:50%; background:var(--unavailable); }
    .ok .dot,.dot.ok { background:var(--ok); } .warning .dot,.dot.warning { background:var(--warning); } .critical .dot,.dot.critical { background:var(--critical); }
    .bar { height:8px; background:#202a39; border-radius:999px; overflow:hidden; margin-top:10px; }
    .bar > span { display:block; height:100%; border-radius:inherit; background:var(--ok); }
    .bar > span.warning { background:var(--warning); } .bar > span.critical { background:var(--critical); }
    #connection.failed { color:var(--critical); }
    @media (max-width:760px) { header { display:block; } .card,.wide { grid-column:1/-1; } main { width:min(100% - 22px,1120px); padding-top:22px; } }
  </style>
</head>
<body>
<main>
  <header><div><h1>N5105 Dashboard</h1><p id="summary">正在读取主机状态</p></div><div><span class="badge" id="connection"><span class="dot"></span>连接中</span><p id="updated"></p></div></header>
  <section class="grid">
    <article class="card wide"><h2>CPU</h2><div id="cpu"></div></article>
    <article class="card wide"><h2>温度</h2><div id="temperatures"></div></article>
    <article class="card"><h2>内存与 Swap</h2><div id="memory"></div></article>
    <article class="card"><h2>磁盘</h2><div id="disk"></div></article>
    <article class="card"><h2>服务</h2><div id="services"></div></article>
  </section>
</main>
<script>
let previousData = null;
const byId = id => document.getElementById(id);
const value = (v, suffix="") => v === null || v === undefined ? "不可用" : `${v}${suffix}`;
const bytes = n => n === null || n === undefined ? "不可用" : `${(n / 1073741824).toFixed(1)} GiB`;
const uptime = s => s === null || s === undefined ? "不可用" : `${Math.floor(s/86400)}天 ${Math.floor((s%86400)/3600)}小时`;
const row = (label, content) => `<div class="metric"><span>${label}</span><span class="value">${content}</span></div>`;
const bar = item => `<div class="bar"><span class="${item.status}" style="width:${Math.min(item.usage_percent || 0,100)}%"></span></div>`;

function render(data) {
  previousData = data;
  byId("summary").textContent = `${data.host.hostname} · ${data.host.os} · 已运行 ${uptime(data.host.uptime_seconds)}`;
  byId("updated").textContent = `更新：${new Date(data.collected_at).toLocaleString()}`;
  byId("connection").className = `badge ${data.overall_status}`;
  byId("connection").innerHTML = `<span class="dot"></span>${data.overall_status === "ok" ? "运行正常" : data.overall_status === "warning" ? "需要关注" : "存在异常"}`;
  byId("cpu").innerHTML = row("使用率", value(data.cpu.usage_percent,"%")) + bar(data.cpu) + row("负载 1/5/15m", data.cpu.load ? `${data.cpu.load["1m"]} / ${data.cpu.load["5m"]} / ${data.cpu.load["15m"]}` : "不可用") + row("频率", value(data.cpu.frequency_mhz," MHz")) + row("逻辑核心", value(data.cpu.logical_cpus));
  byId("temperatures").innerHTML = data.temperatures.length ? data.temperatures.map(t => row(t.name, `<span class="${t.status}">${value(t.celsius,"°C")}</span>`)).join("") : row("传感器","不可用");
  byId("memory").innerHTML = row("内存", `${bytes(data.memory.used_bytes)} / ${bytes(data.memory.total_bytes)}`) + row("使用率", value(data.memory.usage_percent,"%")) + bar(data.memory) + row("Swap", `${bytes(data.swap.used_bytes)} / ${bytes(data.swap.total_bytes)}`);
  byId("disk").innerHTML = row("根分区", `${bytes(data.disk.used_bytes)} / ${bytes(data.disk.total_bytes)}`) + row("使用率", value(data.disk.usage_percent,"%")) + bar(data.disk);
  byId("services").innerHTML = data.services.length ? data.services.map(s => row(s.name, `<span class="badge ${s.status}"><span class="dot"></span>${s.state}</span>`)).join("") : row("服务","未配置");
}

async function refresh() {
  try {
    const response = await fetch("/api/status", {cache: "no-store"});
    if (!response.ok) throw new Error("status request failed");
    render(await response.json());
  } catch (error) {
    byId("connection").className = "badge failed";
    byId("connection").innerHTML = '<span class="dot critical"></span>连接中断';
    byId("updated").textContent = `刷新失败：${new Date().toLocaleString()} · 10秒后重试`;
    if (!previousData) byId("summary").textContent = "暂时无法读取主机状态";
  }
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
```

- [ ] **Step 3: Run route and full tests**

```bash
.venv/bin/pytest tests/test_app.py -v
.venv/bin/pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit the interface**

```bash
git add templates/dashboard.html tests/test_app.py
git commit -m "feat: add responsive status dashboard"
```

---

### Task 6: Deployment Assets and Documentation

**Files:**
- Create: `deploy/n5105-dashboard.service`
- Create: `tests/test_deployment.py`
- Create: `README.md`

**Interfaces:**
- Produces: a systemd unit that runs `.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8001` as `ubuntu`.

- [ ] **Step 1: Write failing deployment-contract tests**

Create `tests/test_deployment.py`:

```python
from pathlib import Path


def test_systemd_unit_is_unprivileged_loopback_only_and_read_only():
    unit = Path("deploy/n5105-dashboard.service").read_text(encoding="utf-8")
    assert "User=ubuntu" in unit
    assert "Group=ubuntu" in unit
    assert "--host 127.0.0.1 --port 8001" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=read-only" in unit
    assert "PYTHONDONTWRITEBYTECODE=1" in unit
```

Run:

```bash
.venv/bin/pytest tests/test_deployment.py -v
```

Expected: failure because the service unit does not exist.

- [ ] **Step 2: Create the hardened unit**

Create `deploy/n5105-dashboard.service`:

```ini
[Unit]
Description=N5105 Read-Only Status Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/projects/n5105-dashboard
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/home/ubuntu/projects/n5105-dashboard/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8001
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Create operational documentation**

Create `README.md` with these exact sections and commands:

````markdown
# N5105 Status Dashboard

Read-only live system status for `dashboard.sonab.uk`.

## Local verification

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8001
```

## Monitored services

Edit `config/services.json`, add an object with `name` and a `.service` unit,
then restart `n5105-dashboard.service`. The dashboard is read-only and cannot
control the configured units.

## Deployment checks

```bash
systemctl status n5105-dashboard --no-pager
curl --fail http://127.0.0.1:8001/healthz
ss -ltnp 'sport = :8001'
```
````

- [ ] **Step 4: Verify assets and commit**

```bash
.venv/bin/pytest -q
systemd-analyze verify deploy/n5105-dashboard.service
git add deploy/n5105-dashboard.service tests/test_deployment.py README.md
git commit -m "chore: add dashboard deployment assets"
```

Expected: tests pass, `systemd-analyze verify` exits 0, and the commit is created.

---

### Task 7: Live Verification and Activation

**Files:**
- Modify outside repository: `/etc/systemd/system/n5105-dashboard.service`
- Modify outside repository: `/etc/cloudflared/config.yml`
- External configuration: Cloudflare DNS and Access policy

**Interfaces:**
- Consumes: the verified repository and deployment unit from Tasks 1–6.
- Produces: authenticated `https://dashboard.sonab.uk` and loopback-only origin `127.0.0.1:8001`.

- [ ] **Step 1: Run fresh pre-deployment verification**

```bash
cd /home/ubuntu/projects/n5105-dashboard
.venv/bin/pytest -q
git status --short --branch
```

Expected: all tests pass and the branch is clean.

- [ ] **Step 2: Verify the live app temporarily before installing systemd**

Run in one terminal:

```bash
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8001
```

Run in another terminal:

```bash
curl --fail http://127.0.0.1:8001/healthz
curl --fail http://127.0.0.1:8001/api/status
curl --fail http://127.0.0.1:8001/
```

Expected: health JSON is `{"status":"ok"}`, status JSON contains the N5105 values and configured services, and the page contains `N5105 Dashboard`. Stop the temporary Uvicorn process before continuing.

- [ ] **Step 3: Install and start the systemd service with user-entered sudo authentication**

Because this host does not have passwordless sudo, the owner runs:

```bash
sudo install -m 0644 deploy/n5105-dashboard.service /etc/systemd/system/n5105-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now n5105-dashboard.service
```

Then verify:

```bash
systemctl is-active n5105-dashboard.service
curl --fail http://127.0.0.1:8001/healthz
ss -ltnp 'sport = :8001'
```

Expected: service is `active`, health returns 200, and the only listening address is `127.0.0.1:8001`.

- [ ] **Step 4: Add and validate the Tunnel ingress rule**

First back up and confirm the catch-all line:

```bash
sudo cp /etc/cloudflared/config.yml /etc/cloudflared/config.yml.pre-dashboard
grep -n '^  - service: http_status:404$' /etc/cloudflared/config.yml
```

Insert the new rule immediately before the catch-all:

```bash
sudo sed -i '/^  - service: http_status:404$/i\
  - hostname: dashboard.sonab.uk\
    service: http://localhost:8001' /etc/cloudflared/config.yml
```

Validate before restart:

```bash
/usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
/usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel ingress rule https://dashboard.sonab.uk
```

Expected: configuration is valid and the URL matches `http://localhost:8001`.

Then the owner runs:

```bash
sudo systemctl restart cloudflared.service
systemctl is-active cloudflared.service
systemctl is-active unionpay-rate.service
```

Expected: both services report `active`. If validation or restart fails, restore with `sudo cp /etc/cloudflared/config.yml.pre-dashboard /etc/cloudflared/config.yml` and restart `cloudflared`.

- [ ] **Step 5: Create the DNS route**

Run as `ubuntu`:

```bash
cloudflared tunnel route dns 3eaec435-410f-461c-958b-fdccb6c092e7 dashboard.sonab.uk
```

Expected: Cloudflare confirms a CNAME route for `dashboard.sonab.uk`. If the record already exists and points to this tunnel, treat that as success.

- [ ] **Step 6: Configure Cloudflare Access before normal use**

In Cloudflare Zero Trust:

1. Open **Access controls → Applications → Add an application → Self-hosted**.
2. Set the application name to `N5105 Dashboard`.
3. Set the public hostname to `dashboard.sonab.uk` and leave the path empty so every route is protected.
4. Set session duration to `24 hours`.
5. Add an Allow policy named `Owner only`.
6. Under Include, choose **Emails** and enter the owner's email address.
7. Enable **One-Time PIN** as the login method and save the application.

Expected: a signed-out browser is redirected to Cloudflare Access and only the allowed email can request a PIN.

- [ ] **Step 7: Run final end-to-end checks**

From the Mac before authenticating:

```bash
curl -I https://dashboard.sonab.uk/
curl -I https://dashboard.sonab.uk/api/status
```

Expected: both requests redirect to or are rejected by Cloudflare Access, not the origin dashboard.

After authenticating in a private browser window:

- Confirm the dashboard loads on phone and desktop widths.
- Confirm values refresh after 10 seconds.
- Confirm CPU Package, Core 0–3, and NVMe temperatures appear.
- Confirm UnionPay Rate and Cloudflare Tunnel are active.
- Confirm stopping no services and executing no controls is possible from the page.

Finally run on N5105:

```bash
systemctl is-active n5105-dashboard.service cloudflared.service unionpay-rate.service
curl --fail http://127.0.0.1:8001/healthz
curl --fail http://127.0.0.1:8000/
git -C /home/ubuntu/projects/unionpay-rate status --short --branch
```

Expected: all three services are active, both local HTTP checks succeed, and the UnionPay repository remains clean and unchanged.
