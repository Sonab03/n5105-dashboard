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
    assert "n5105-power-sampler.service" in directives["After"][0].split()
    assert "n5105-power-sampler.service" in directives["Wants"][0].split()
    assert "Requires" not in directives


def test_power_sampler_unit_has_minimal_root_boundary_and_no_network_dependency():
    unit = Path("deploy/n5105-power-sampler.service").read_text(encoding="utf-8")
    directives = active_directives(unit)
    assert directives["User"] == ["root"]
    assert directives["Group"] == ["ubuntu"]
    assert directives["ExecStart"] == [
        "/usr/bin/python3 /usr/local/libexec/n5105-power-sampler"
    ]
    assert directives["RuntimeDirectory"] == ["n5105-dashboard"]
    assert directives["RuntimeDirectoryMode"] == ["0750"]
    assert directives["UMask"] == ["0027"]
    assert directives["Restart"] == ["on-failure"]
    assert directives["RestartSec"] == ["3"]
    assert directives["NoNewPrivileges"] == ["true"]
    assert directives["PrivateTmp"] == ["true"]
    assert directives["PrivateDevices"] == ["true"]
    assert directives["ProtectSystem"] == ["strict"]
    assert directives["ProtectHome"] == ["true"]
    assert directives["ProtectKernelTunables"] == ["true"]
    assert directives["ProtectKernelModules"] == ["true"]
    assert directives["ProtectControlGroups"] == ["true"]
    assert directives["ReadWritePaths"] == ["/run/n5105-dashboard"]
    assert directives["RestrictAddressFamilies"] == ["AF_UNIX"]
    assert "After" not in directives
    assert "Wants" not in directives
    assert "Requires" not in directives


def test_active_directive_parser_rejects_commented_or_extra_execstart():
    assert active_directives("#ExecStart=/bad\nExecStart=/expected")["ExecStart"] == ["/expected"]
    assert active_directives("ExecStart=/expected --extra")["ExecStart"] != ["/expected"]
