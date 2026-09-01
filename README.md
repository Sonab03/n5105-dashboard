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

## Install and operate the systemd service

```bash
sudo install -o root -g root -m 0644 deploy/n5105-dashboard.service /etc/systemd/system/n5105-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now n5105-dashboard.service
sudo systemctl status n5105-dashboard.service --no-pager
sudo journalctl -u n5105-dashboard.service -n 100 --no-pager
```

For a safe update, copy the reviewed unit into place, validate it, then reload
and restart:

```bash
sudo install -o root -g root -m 0644 deploy/n5105-dashboard.service /etc/systemd/system/n5105-dashboard.service
sudo systemd-analyze verify /etc/systemd/system/n5105-dashboard.service
sudo systemctl daemon-reload
sudo systemctl restart n5105-dashboard.service
sudo systemctl status n5105-dashboard.service --no-pager
```

Before an update, save the current unit. If validation or restart fails, restore
the backup and reload the manager:

```bash
sudo cp -a /etc/systemd/system/n5105-dashboard.service /etc/systemd/system/n5105-dashboard.service.bak
sudo cp -a /etc/systemd/system/n5105-dashboard.service.bak /etc/systemd/system/n5105-dashboard.service
sudo systemctl daemon-reload
sudo systemctl restart n5105-dashboard.service
```

## Cloudflare Tunnel and Access setup

Perform these steps manually on the N5105; this document does not make live
changes. Back up the existing Cloudflare Tunnel configuration first. Preserve
the existing `rate.sonab.uk` ingress rule. Insert
`dashboard.sonab.uk -> http://localhost:8001` immediately before the catch-all
rule, validate the configuration, and restart the tunnel only after validation
succeeds. If validation or restart fails, restore the backup and retry.

Create the DNS route for `dashboard.sonab.uk` using Tunnel ID
`3eaec435-410f-461c-958b-fdccb6c092e7`.

In Cloudflare Zero Trust, create an Access application covering
`dashboard.sonab.uk/*`. Configure One-Time PIN authentication, an allow-only
policy with exactly one allowed email address, and a 24-hour session duration.
Verify that every dashboard route, including `/`, `/healthz`, and `/api/status`,
is protected by the Access application.
