# Task 2 Report: Extensible systemd Service Monitoring

## Files changed

- `system_metrics.py`: added service unit validation/config loading and safe `systemctl is-active` collection.
- `tests/test_system_metrics.py`: added configuration, inactive-state, and failure sanitization tests.
- `config/services.json`: added UnionPay Rate and Cloudflare Tunnel service targets.

## TDD evidence

- RED: `PYTHONPATH=. .venv/bin/pytest tests/test_system_metrics.py -k service -v` — collection failed because `collect_services` was not yet defined.
- GREEN focused: same command after implementation — 3 passed, 3 deselected.
- GREEN full: `PYTHONPATH=. .venv/bin/pytest -q` — 6 passed.

The requested command without `PYTHONPATH=.` could not import the project module in this macOS Python 3.8 environment; the equivalent command with the project root on the import path produced the intended RED and GREEN results.

## Totals, deviations, concerns

Six tests passed in the full suite. No functional deviations from the brief. Service units are regex-validated and passed to subprocess as an argument list with `shell=False`; raw subprocess errors are not exposed. Runtime target Python 3.12.3 was not available in this local environment (tests ran under Python 3.8.2).
