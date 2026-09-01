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
