# N5105 Status Dashboard Design

**Date:** 2026-08-31  
**Status:** Approved for implementation planning

## 1. Purpose

Build a new, independent status dashboard for the N5105 host at
`dashboard.sonab.uk`. The dashboard provides a read-only view of current
hardware, operating-system, storage, and selected systemd service health.

This is not part of the existing UnionPay rate application. The existing
`/home/ubuntu/projects/unionpay-rate` repository, UI, service, and
`rate.sonab.uk` routing remain unchanged.

## 2. Goals

- Show the current N5105 state in a responsive browser dashboard.
- Refresh status immediately on page load and every 10 seconds afterward.
- Expose CPU usage, load, frequency, temperatures, memory, swap, root-disk
  usage, system uptime, and configured service states.
- Protect every dashboard route with Cloudflare Access.
- Keep the origin reachable only through the existing Cloudflare Tunnel.
- Make the monitored systemd service list extensible through configuration.
- Degrade gracefully when an individual metric cannot be collected.

## 3. Non-goals

- No historical metric storage or trend charts in the first release.
- No alert delivery.
- No process list, network-address display, or arbitrary command output.
- No start, stop, restart, shutdown, or other system-control actions.
- No changes to the UnionPay rate project.

## 4. Deployment Architecture

The new repository lives at:

```text
/home/ubuntu/projects/n5105-dashboard
```

The application runs as a separate systemd service named
`n5105-dashboard.service`, under the unprivileged `ubuntu` account. Uvicorn
binds only to `127.0.0.1:8001`.

The existing Cloudflare Tunnel receives one additional ingress rule:

```text
dashboard.sonab.uk -> http://localhost:8001
```

Cloudflare Access protects `dashboard.sonab.uk/*` before requests reach the
origin. The policy uses One-Time PIN authentication, allows only the owner's
configured email address, and uses a 24-hour session duration. The email
address is deployment configuration and is never committed to the repository.

Request flow:

```text
Browser
  -> Cloudflare Access
  -> Cloudflare Tunnel
  -> 127.0.0.1:8001
  -> FastAPI dashboard
  -> read-only operating-system interfaces
```

## 5. Project Structure

```text
n5105-dashboard/
├── app.py
├── system_metrics.py
├── config/
│   └── services.json
├── templates/
│   └── dashboard.html
├── tests/
│   ├── test_app.py
│   └── test_system_metrics.py
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-08-31-n5105-status-dashboard-design.md
├── requirements.txt
├── .gitignore
└── README.md
```

Responsibilities:

- `app.py`: FastAPI routes and response headers.
- `system_metrics.py`: metric collection, validation, threshold evaluation,
  and response assembly.
- `config/services.json`: user-editable list of monitored systemd units.
- `templates/dashboard.html`: responsive UI and 10-second polling logic.
- `tests/`: route, collector, configuration, threshold, and failure tests.

## 6. Routes

### `GET /`

Returns the dashboard HTML. The initial document contains the page shell and
loading state. JavaScript requests current data from `/api/status` immediately.

### `GET /api/status`

Returns the current status as JSON. The response includes `Cache-Control:
no-store` and does not expose source paths, raw command output, IP addresses,
or process information.

The top-level response fields are:

```json
{
  "collected_at": "2026-08-31T23:50:00+09:00",
  "overall_status": "ok",
  "host": {},
  "cpu": {},
  "temperatures": [],
  "memory": {},
  "swap": {},
  "disk": {},
  "services": [],
  "errors": []
}
```

`overall_status` is `ok`, `warning`, or `critical`. Each metric or service also
contains its own status so the UI can color it independently.

### `GET /healthz`

Returns a minimal application-liveness response. It verifies that the
dashboard process can handle requests; it does not treat a stopped monitored
service as a dashboard failure.

## 7. Metrics

The dashboard displays:

- Hostname, Ubuntu version, collection time, and system uptime.
- Current total CPU utilization.
- 1-, 5-, and 15-minute load averages.
- Current CPU frequency and four logical-CPU count.
- CPU Package temperature and Core 0 through Core 3 temperatures.
- NVMe Composite temperature.
- Used and total memory, plus memory utilization.
- Used and total swap, plus swap utilization.
- Used and total bytes for the root filesystem, plus disk utilization.
- State of every configured systemd service.

`psutil` supplies CPU, memory, swap, disk, frequency, and boot-time information.
The standard library reads the hostname and `/etc/os-release` supplies the
Ubuntu version. Temperature data is read from Linux hwmon/sysfs nodes to
preserve stable names for `Package id 0`, individual cores, and NVMe
`Composite`. Missing sensors are reported as unavailable instead of failing the
whole response.

## 8. Thresholds

| Metric | Warning | Critical |
|---|---:|---:|
| CPU utilization | 70% | 90% |
| CPU Package/Core temperature | 70 C | 85 C |
| NVMe temperature | 60 C | 75 C |
| Memory utilization | 80% | 90% |
| Root-disk utilization | 80% | 90% |
| Monitored service | n/a | not active |

Swap usage is displayed but does not affect overall health in the first
release. Load averages and CPU frequency are informational. A critical metric
takes precedence over warnings when calculating `overall_status`.

## 9. Extensible Service Monitoring

The initial `config/services.json` contains:

```json
[
  {
    "name": "UnionPay Rate",
    "unit": "unionpay-rate.service"
  },
  {
    "name": "Cloudflare Tunnel",
    "unit": "cloudflared.service"
  }
]
```

Adding a service requires only another object in this file followed by a
dashboard-service restart. The UI renders the returned service list
dynamically, so neither Python nor HTML changes are required.

Service unit values must match a restrictive systemd-unit-name pattern. The
collector invokes `systemctl is-active -- <unit>` as an argument list without
a shell. Invalid entries are rejected and reported without running a command.
The dashboard never exposes a control operation.

## 10. User Interface

The dashboard uses a dark, responsive card layout suitable for phone and
desktop widths.

The header shows hostname, last successful update, uptime, and overall health.
Cards group CPU, temperatures, memory/swap, disk, and services. Normal values
use green accents, warnings use yellow, critical states use red, and unavailable
values use a neutral style.

The page requests `/api/status` immediately and every 10 seconds. While a
request is in progress, the existing values remain visible. If refreshing
fails, the last successful values remain visible and the header shows the
failure time and a connection-interrupted indicator. The next scheduled poll
retries automatically.

## 11. Failure Handling

Each collector is isolated. A sensor, filesystem, service, or psutil failure
adds a sanitized entry to `errors` and marks only the affected item as
unavailable. Other successfully collected fields remain present.

If all core system collectors fail, `/api/status` still returns a valid JSON
shape with `overall_status` set to `critical`. Unexpected implementation errors
are logged by the service but are not returned verbatim to the browser.

The service-configuration file is loaded and validated at application startup.
An absent or malformed file produces an empty monitored-service list and a
sanitized configuration error; it does not prevent the system dashboard from
starting.

## 12. Security

- The origin binds only to `127.0.0.1:8001`.
- Cloudflare Access protects the page, API, and health route.
- The process runs as the unprivileged `ubuntu` user.
- Collection uses read-only `/proc`, `/sys`, psutil, and fixed systemctl calls.
- No route accepts a command, path, service name, or other collection target.
- Responses use `Cache-Control: no-store`.
- No secrets, Access emails, Cloudflare credentials, raw exceptions, process
  lists, or network addresses are committed or returned.

## 13. Testing

Automated tests cover:

- CPU, memory, swap, disk, uptime, and frequency normalization.
- CPU Package, individual-core, and NVMe temperature parsing.
- Warning and critical threshold boundaries.
- Valid service configuration, invalid unit rejection, and failed service
  queries.
- Partial collector failures and sanitized error responses.
- Dynamic service arrays.
- `/`, `/api/status`, and `/healthz` response behavior.
- `Cache-Control: no-store` on status responses.

Collectors use injected filesystem readers and command runners in tests so the
test suite is deterministic and never controls real services.

## 14. Deployment and Verification

Deployment proceeds in small verified commits. Before activation:

1. Run the full automated test suite.
2. Start the app temporarily on `127.0.0.1:8001` and inspect all three routes.
3. Confirm live sensor labels and values on the N5105.
4. Install and start `n5105-dashboard.service`.
5. Confirm port 8001 is bound only to the loopback interface.
6. Add the `dashboard.sonab.uk` ingress rule to the existing Tunnel and restart
   `cloudflared`.
7. Create the Cloudflare Access application and allow-only policy.
8. Confirm unauthenticated requests are blocked and an allowed One-Time PIN
   session can load both the page and `/api/status`.
9. Confirm `rate.sonab.uk` and `unionpay-rate.service` remain unchanged and
   healthy.

Rollback removes the new Tunnel ingress rule and stops/disables
`n5105-dashboard.service`. It does not require changing the rate application.
