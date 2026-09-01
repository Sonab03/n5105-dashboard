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
systemctl status n5105-power-sampler --no-pager
systemctl status n5105-dashboard --no-pager
sudo -u ubuntu python3 -m json.tool /run/n5105-dashboard/power.json
stat -c '%U:%G %a %n' /run/n5105-dashboard/power.json
curl --fail http://127.0.0.1:8001/healthz
curl --fail http://127.0.0.1:8001/api/status
ss -ltnp 'sport = :8001'
```

## Install and operate the systemd service

The root sampler must run only from its root-owned installed copy. Validate both
units, install the sampler and units, then start the sampler before restarting
the unprivileged dashboard:

```bash
sudo install -d -o root -g root -m 0755 /usr/local/libexec
sudo install -o root -g root -m 0755 power_sampler.py /usr/local/libexec/n5105-power-sampler
systemd-analyze verify deploy/n5105-power-sampler.service deploy/n5105-dashboard.service
sudo install -o root -g root -m 0644 deploy/n5105-power-sampler.service /etc/systemd/system/n5105-power-sampler.service
sudo install -o root -g root -m 0644 deploy/n5105-dashboard.service /etc/systemd/system/n5105-dashboard.service
sudo systemd-analyze verify /etc/systemd/system/n5105-power-sampler.service /etc/systemd/system/n5105-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now n5105-power-sampler.service
sudo systemctl restart n5105-dashboard.service
systemctl is-active n5105-power-sampler.service n5105-dashboard.service
sudo -u ubuntu python3 -m json.tool /run/n5105-dashboard/power.json
stat -c '%U:%G %a %n' /run/n5105-dashboard/power.json
curl --fail http://127.0.0.1:8001/healthz
curl --fail http://127.0.0.1:8001/api/status
```

Before every update, back up the installed sampler and units. Install, validate,
reload, restart, and test in that order:

```bash
sudo test -f /usr/local/libexec/n5105-power-sampler
sudo test -f /etc/systemd/system/n5105-power-sampler.service
sudo test -f /etc/systemd/system/n5105-dashboard.service
sudo cp -a /usr/local/libexec/n5105-power-sampler /usr/local/libexec/n5105-power-sampler.pre-update
sudo cp -a /etc/systemd/system/n5105-power-sampler.service /etc/systemd/system/n5105-power-sampler.service.pre-update
sudo cp -a /etc/systemd/system/n5105-dashboard.service /etc/systemd/system/n5105-dashboard.service.pre-update
sudo install -o root -g root -m 0755 power_sampler.py /usr/local/libexec/n5105-power-sampler
sudo install -o root -g root -m 0644 deploy/n5105-power-sampler.service /etc/systemd/system/n5105-power-sampler.service
sudo install -o root -g root -m 0644 deploy/n5105-dashboard.service /etc/systemd/system/n5105-dashboard.service
sudo systemd-analyze verify /etc/systemd/system/n5105-power-sampler.service /etc/systemd/system/n5105-dashboard.service
sudo systemctl daemon-reload
sudo systemctl restart n5105-power-sampler.service
sudo systemctl restart n5105-dashboard.service
systemctl is-active n5105-power-sampler.service n5105-dashboard.service
sudo -u ubuntu python3 -m json.tool /run/n5105-dashboard/power.json
curl --fail http://127.0.0.1:8001/healthz
```

If validation, restart, or the health check fails, restore the dashboard first,
then restore the sampler. This feature requires no Tunnel or Access changes:

```bash
sudo cp -a /etc/systemd/system/n5105-dashboard.service.pre-update /etc/systemd/system/n5105-dashboard.service
sudo cp -a /usr/local/libexec/n5105-power-sampler.pre-update /usr/local/libexec/n5105-power-sampler
sudo cp -a /etc/systemd/system/n5105-power-sampler.service.pre-update /etc/systemd/system/n5105-power-sampler.service
sudo systemd-analyze verify /etc/systemd/system/n5105-power-sampler.service /etc/systemd/system/n5105-dashboard.service
sudo systemctl daemon-reload
sudo systemctl restart n5105-dashboard.service
sudo systemctl restart n5105-power-sampler.service
systemctl is-active n5105-dashboard.service n5105-power-sampler.service
curl --fail http://127.0.0.1:8001/healthz
```

## Cloudflare Tunnel and Access setup

Perform these steps manually on the N5105; this document does not make live
changes. Back up the existing Cloudflare Tunnel configuration before editing
it, and verify that the backup contains the working `rate.sonab.uk` route:

```bash
sudo cp -a /etc/cloudflared/config.yml /etc/cloudflared/config.yml.pre-dashboard
/usr/bin/cloudflared --config /etc/cloudflared/config.yml.pre-dashboard tunnel ingress validate
/usr/bin/cloudflared --config /etc/cloudflared/config.yml.pre-dashboard tunnel ingress rule https://rate.sonab.uk
```

Insert `dashboard.sonab.uk -> http://localhost:8001` immediately before the
catch-all rule without changing the existing `rate.sonab.uk` ingress. Validate
both routes before restarting the Tunnel, then verify the Tunnel and rate
service afterward:

```bash
/usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
/usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel ingress rule https://dashboard.sonab.uk
/usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel ingress rule https://rate.sonab.uk
sudo systemctl restart cloudflared.service
systemctl is-active cloudflared.service
systemctl is-active unionpay-rate.service
curl --fail http://127.0.0.1:8000/
```

If validation, restart, or either post-change service check fails, restore the
Tunnel backup immediately, validate it, restart `cloudflared`, and recheck the
rate service before doing anything else:

```bash
sudo cp -a /etc/cloudflared/config.yml.pre-dashboard /etc/cloudflared/config.yml
/usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
/usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel ingress rule https://rate.sonab.uk
sudo systemctl restart cloudflared.service
systemctl is-active cloudflared.service
systemctl is-active unionpay-rate.service
curl --fail http://127.0.0.1:8000/
```

Create the DNS route for `dashboard.sonab.uk` using Tunnel ID
`3eaec435-410f-461c-958b-fdccb6c092e7`.

In Cloudflare Zero Trust, create an Access application covering
`dashboard.sonab.uk/*`. Configure One-Time PIN authentication, an allow-only
policy with exactly one allowed email address, and a 24-hour session duration.
Verify that every dashboard route, including `/`, `/healthz`, and `/api/status`,
is protected by the Access application.

## Full rollback

Stop and disable the dashboard first. Restore the pre-dashboard Tunnel backup;
this removes the dashboard ingress while preserving the original
`rate.sonab.uk` ingress. Validate the restored configuration before restarting
`cloudflared`, and verify the rate service at the end:

```bash
sudo systemctl disable --now n5105-dashboard.service
systemctl is-active n5105-dashboard.service
sudo cp -a /etc/cloudflared/config.yml.pre-dashboard /etc/cloudflared/config.yml
/usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
/usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel ingress rule https://rate.sonab.uk
sudo systemctl restart cloudflared.service
systemctl is-active cloudflared.service
systemctl is-active unionpay-rate.service
curl --fail http://127.0.0.1:8000/
```

Remove the `dashboard.sonab.uk` DNS route and Cloudflare Access application
after the local rollback is verified. Do not remove or modify the
`rate.sonab.uk` DNS route, Tunnel ingress, service, or application.
