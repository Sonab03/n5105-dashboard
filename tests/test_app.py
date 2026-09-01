import json

from fastapi.testclient import TestClient

from app import create_app


SAMPLE = {
    "collected_at": "2026-09-01T00:00:00+09:00",
    "overall_status": "ok",
    "host": {"hostname": "ubuntu-n5105", "os": "Ubuntu 24.04.2 LTS", "uptime_seconds": 3600},
    "cpu": {"usage_percent": 25.0, "status": "ok", "load": {"1m": 0.1, "5m": 0.2, "15m": 0.3}, "frequency_mhz": 1800, "logical_cpus": 4},
    "power": {"package_watts": 5.8, "sample_seconds": 5.0, "status": "ok", "estimated": True},
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
    assert response.headers["cache-control"] == "no-store"
    assert "N5105 Dashboard" in response.text
    for element_id in ("summary", "cpu", "temperatures", "memory", "disk", "services", "connection", "updated"):
        assert f'id="{element_id}"' in response.text
    assert "setInterval(refresh, 5000)" in response.text
    assert "setInterval(refresh, 10000)" not in response.text
    assert "5秒后重试" in response.text
    assert "10秒后重试" not in response.text
    assert 'metric("CPU Package 功耗"' in response.text
    assert "秒平均·估算" in response.text
    assert 'fetch("/api/status", {cache: "no-store"})' in response.text
    assert "previousData" in response.text
    assert "textContent" in response.text
    assert "innerHTML" not in response.text
    assert 'aria-live="polite"' in response.text
    for status, color in (
        ("ok", "var(--ok)"),
        ("warning", "var(--warning)"),
        ("critical", "var(--critical)"),
        ("unavailable", "var(--unavailable)"),
    ):
        assert f".value.{status} {{ color:{color}; }}" in response.text
        assert f".badge.{status} {{ color:{color};" in response.text
    assert "function statusBadge(state, status)" in response.text
    assert "statusBadge(available(item.state), status)" in response.text
    assert "temperatureCard.append(metric(available(item.name), value(item.celsius, \"°C\"), item.status))" in response.text
    assert "function serviceStatus(status, state)" in response.text
    assert 'serviceStatus(item.status, item.state)' in response.text


def test_status_route_returns_provider_payload_without_cache():
    response = make_client().get("/api/status")
    assert response.status_code == 200
    assert response.json() == SAMPLE
    assert response.headers["cache-control"] == "no-store"


def test_health_route_checks_only_dashboard_liveness():
    response = make_client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_default_status_provider_keeps_service_targets_until_application_restart(tmp_path):
    config = tmp_path / "services.json"
    config.write_text(
        json.dumps([{"name": "Initial", "unit": "initial.service"}]),
        encoding="utf-8",
    )

    def echo_config(*, service_targets, service_config_errors):
        return {"services": service_targets, "errors": service_config_errors}

    client = TestClient(
        create_app(
            service_config_path=config,
            status_collector=echo_config,
        )
    )
    config.write_text(
        json.dumps([{"name": "Edited", "unit": "edited.service"}]),
        encoding="utf-8",
    )

    assert client.get("/api/status").json() == {
        "services": [{"name": "Initial", "unit": "initial.service"}],
        "errors": [],
    }
    assert client.get("/api/status").json() == {
        "services": [{"name": "Initial", "unit": "initial.service"}],
        "errors": [],
    }

    restarted_client = TestClient(
        create_app(
            service_config_path=config,
            status_collector=echo_config,
        )
    )
    assert restarted_client.get("/api/status").json() == {
        "services": [{"name": "Edited", "unit": "edited.service"}],
        "errors": [],
    }


def test_default_status_provider_keeps_startup_config_error_until_restart(tmp_path):
    config = tmp_path / "services.json"
    config.write_text("not-json", encoding="utf-8")

    def echo_config(*, service_targets, service_config_errors):
        return {"services": service_targets, "errors": service_config_errors}

    client = TestClient(
        create_app(
            service_config_path=config,
            status_collector=echo_config,
        )
    )
    config.write_text("[]", encoding="utf-8")

    assert client.get("/api/status").json() == {
        "services": [],
        "errors": ["service configuration unavailable"],
    }

    restarted_client = TestClient(
        create_app(
            service_config_path=config,
            status_collector=echo_config,
        )
    )
    assert restarted_client.get("/api/status").json() == {
        "services": [],
        "errors": [],
    }
