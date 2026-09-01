# CPU Package Power Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe 5-second CPU Package power estimate to the existing N5105 dashboard.

**Architecture:** A root-only standard-library sampler reads fixed Intel RAPL files and atomically publishes `/run/n5105-dashboard/power.json`. The unprivileged FastAPI collector validates that file and exposes an informational `power` object; the existing page renders it and refreshes every 5 seconds.

**Tech Stack:** Python 3.12, FastAPI, pytest, systemd, Intel RAPL sysfs

**Spec:** `docs/superpowers/specs/2026-09-02-cpu-package-power-design.md`

## Global Constraints

- Keep `n5105-dashboard.service` as `ubuntu`; only the isolated sampler runs as root.
- Install sampler code as root-owned `/usr/local/libexec/n5105-power-sampler`; never execute the checkout copy as root.
- Sample and refresh every 5 seconds; samples older than 15 seconds or over 5 seconds in the future are unavailable.
- Power is informational and never changes `overall_status`.
- Use only Python's standard library in the sampler; no shell, external commands, sockets, history, Cloudflare changes, or whole-system-power claims.

---

### Task 1: RAPL sampler

**Files:**
- Create: `power_sampler.py`
- Create: `tests/test_power_sampler.py`

**Interfaces:**
- Produces: `calculate_package_watts(first_uj: int, second_uj: int, max_uj: int, elapsed_seconds: float) -> float`
- Produces: `build_sample(...) -> dict`, `publish_sample(path: Path, sample: dict) -> None`, and `main() -> None`

- [ ] Write tests for normal delta, actual elapsed time, one wrap, invalid readings/elapsed values, fixed-domain validation, JSON fields, and atomic mode `0640`.
- [ ] Run `.venv/bin/pytest tests/test_power_sampler.py -q` and confirm collection fails because `power_sampler` does not exist.
- [ ] Implement fixed-path readings, monotonic 5-second sampling, RFC 3339 UTC timestamps, finite/non-negative validation, same-directory atomic replacement, retry logging, and no external execution.
- [ ] Run `.venv/bin/pytest tests/test_power_sampler.py -q`; expect all sampler tests to pass.
- [ ] Commit `power_sampler.py` and `tests/test_power_sampler.py` with `feat: add RAPL power sampler`.

### Task 2: Dashboard power-file reader and API payload

**Files:**
- Modify: `system_metrics.py`
- Modify: `tests/test_system_metrics.py`

**Interfaces:**
- Produces: `read_power_sample(path: Path, now: datetime | None = None) -> tuple[dict, list[str]]`
- `collect_status(..., power_path: Path = Path("/run/n5105-dashboard/power.json")) -> dict` adds top-level `power`.

- [ ] Add tests for valid data, missing/malformed/unreadable data, wrong version/source/type, booleans, non-finite/negative values, timestamps over 5 seconds future, timestamps over 15 seconds stale, and unchanged overall health.
- [ ] Run the focused new tests and confirm expected missing-interface failures.
- [ ] Implement strict parsing with stable `ok`/`unavailable` payloads and only `CPU package power unavailable` in public errors.
- [ ] Run `.venv/bin/pytest tests/test_system_metrics.py -q`; expect all collector tests to pass.
- [ ] Commit collector changes with `feat: expose CPU package power status`.

### Task 3: UI and systemd integration

**Files:**
- Modify: `templates/dashboard.html`
- Modify: `tests/test_app.py`
- Create: `deploy/n5105-power-sampler.service`
- Modify: `deploy/n5105-dashboard.service`
- Modify: `tests/test_deployment.py`

**Interfaces:**
- CPU card consumes `data.power.package_watts`, `sample_seconds`, and `status`.
- Sampler unit executes `/usr/bin/python3 /usr/local/libexec/n5105-power-sampler` and owns `/run/n5105-dashboard`.

- [ ] Update tests to require the power row, estimate label, unavailable fallback, 5,000 ms polling/retry copy, root sampler hardening, and dashboard soft `Wants`/`After` without `Requires`.
- [ ] Run `.venv/bin/pytest tests/test_app.py tests/test_deployment.py -q` and confirm the new assertions fail.
- [ ] Implement the CPU row and interval change; add the root sampler unit and soft dashboard ordering exactly as specified.
- [ ] Run `.venv/bin/pytest tests/test_app.py tests/test_deployment.py -q`; expect all focused tests to pass.
- [ ] Commit UI and unit changes with `feat: display five-second package power`.

### Task 4: Operations documentation and verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Documents root-owned install, unit validation, activation, API/file checks, and rollback without changing Cloudflare or UnionPay.

- [ ] Add exact `sudo install`, `systemd-analyze verify`, `systemctl`, `stat`, JSON, health, and rollback commands.
- [ ] Run `.venv/bin/pytest -q`, `python3 -m compileall -q app.py system_metrics.py power_sampler.py`, `git diff --check`, and local unit-content tests.
- [ ] Review the diff against every acceptance criterion in the spec and confirm no Cloudflare or UnionPay files changed.
- [ ] Commit the documentation with `docs: add power sampler operations`.

