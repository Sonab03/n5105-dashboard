from pathlib import Path
from typing import Dict, List


def active_directives(unit: str) -> Dict[str, List[str]]:
    directives: Dict[str, List[str]] = {}
    for line in unit.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        directives.setdefault(key, []).append(value)
    return directives


def test_systemd_unit_is_unprivileged_loopback_only_and_read_only():
    directives = active_directives(Path("deploy/n5105-dashboard.service").read_text(encoding="utf-8"))
    assert directives["User"] == ["ubuntu"]
    assert directives["Group"] == ["ubuntu"]
    assert directives["WorkingDirectory"] == ["/home/ubuntu/projects/n5105-dashboard"]
    assert directives["ExecStart"] == ["/home/ubuntu/projects/n5105-dashboard/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8001"]
    assert directives["Restart"] == ["on-failure"]
    assert directives["RestartSec"] == ["3"]
    assert directives["NoNewPrivileges"] == ["true"]
    assert directives["PrivateTmp"] == ["true"]
    assert directives["ProtectSystem"] == ["strict"]
    assert directives["ProtectHome"] == ["read-only"]
    assert directives["ProtectKernelTunables"] == ["true"]
    assert directives["ProtectKernelModules"] == ["true"]
    assert directives["ProtectControlGroups"] == ["true"]
    assert directives["Environment"] == ["PYTHONUNBUFFERED=1", "PYTHONDONTWRITEBYTECODE=1"]


def test_active_directive_parser_rejects_commented_or_extra_execstart():
    assert active_directives("#ExecStart=/bad\nExecStart=/expected")["ExecStart"] == ["/expected"]
    assert active_directives("ExecStart=/expected --extra")["ExecStart"] != ["/expected"]
