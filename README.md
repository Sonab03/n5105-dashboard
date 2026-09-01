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
