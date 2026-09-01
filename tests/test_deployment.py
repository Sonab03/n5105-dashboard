from pathlib import Path


def test_systemd_unit_is_unprivileged_loopback_only_and_read_only():
    unit = Path("deploy/n5105-dashboard.service").read_text(encoding="utf-8")
    assert "User=ubuntu" in unit
    assert "Group=ubuntu" in unit
    assert "WorkingDirectory=/home/ubuntu/projects/n5105-dashboard" in unit
    assert "ExecStart=/home/ubuntu/projects/n5105-dashboard/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8001" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=3" in unit
    assert "NoNewPrivileges=true" in unit
    assert "PrivateTmp=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=read-only" in unit
    assert "ProtectKernelTunables=true" in unit
    assert "ProtectKernelModules=true" in unit
    assert "ProtectControlGroups=true" in unit
    assert "PYTHONDONTWRITEBYTECODE=1" in unit
