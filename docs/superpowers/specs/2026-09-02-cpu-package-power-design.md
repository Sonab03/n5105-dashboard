# N5105 CPU Package Power Design

**Date:** 2026-09-02
**Status:** Approved in chat; awaiting written-spec review
**Project:** N5105 Status Dashboard

## 1. Context

The deployed dashboard currently reports live CPU utilization, load, frequency,
temperatures, memory, swap, disk, host information, and configured systemd
services. It polls `/api/status` every 10 seconds and runs as the unprivileged
`ubuntu` user.

The N5105 host exposes Intel RAPL energy counters for `package-0` and `core` at
`/sys/devices/virtual/powercap/intel-rapl/`. Both `intel_rapl_common` and
`intel_rapl_msr` are loaded, and `turbostat` is installed. The relevant
`energy_uj` files are `0400 root:root`, so the existing dashboard service cannot
read them. The value is an estimate of CPU package energy, not whole-system
wall power.

## 2. Goals

- Display the N5105 CPU Package's most recent 5-second average power in watts.
- Refresh the entire dashboard every 5 seconds.
- Keep the FastAPI dashboard unprivileged and read-only.
- Isolate root-only RAPL access in a minimal component with no network surface.
- Degrade only the power metric when sampling is unavailable.
- Preserve the existing dashboard, Cloudflare Access, Tunnel, and UnionPay rate
  service behavior.

## 3. Non-goals

- Whole-system wall power, SSD power, memory power, NIC power, or PSU losses.
- Power history, charts, persistence, alerts, or health thresholds.
- Direct browser access to the sampler.
- `sudo turbostat` calls from the web process.
- Granting the web process capabilities or direct RAPL permissions.
- Cloudflare DNS, Tunnel, or Access changes.

## 4. Chosen architecture

Use a separate root sampler that writes a constrained runtime JSON file. The
dashboard reads only that file:

```text
RAPL package-0 energy_uj
          |
          v
n5105-power-sampler.service (root, no network)
          |
          v
/run/n5105-dashboard/power.json (root:ubuntu 0640)
          |
          v
n5105-dashboard.service (ubuntu)
          |
          v
/api/status -> browser
```

The root service must not execute code from the `ubuntu`-writable project
checkout. The versioned sampler source is installed as the root-owned executable
`/usr/local/libexec/n5105-power-sampler` with mode `0755`; the service executes
that installed copy with `/usr/bin/python3`.

Rejected alternatives:

- Changing RAPL sysfs permissions with udev/tmpfiles couples correctness to
  boot and module reload timing and gives the web service direct kernel-counter
  access.
- Calling `sudo turbostat` from each API request expands the sudo and process
  execution surface and adds unnecessary overhead.

## 5. Components and responsibilities

### 5.1 Power sampler

Add a standard-library-only sampler source file to the repository. Its deployed
copy runs continuously under `n5105-power-sampler.service` and owns exactly
these responsibilities:

1. Read the fixed CPU Package files:
   - `/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/energy_uj`
   - `/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/max_energy_range_uj`
2. Confirm the fixed domain name is `package-0`.
3. Take two energy readings separated by 5 seconds measured with a monotonic
   clock.
4. Correct a single counter wrap using `max_energy_range_uj`.
5. Calculate watts as `delta_microjoules / 1_000_000 / elapsed_seconds`.
6. Atomically publish a validated JSON sample.
7. Continue sampling after transient read or write failures without exposing
   raw errors to the dashboard.

The sampler does not invoke a shell, execute external commands, open sockets,
accept input, or read paths supplied by users or configuration.

### 5.2 Dashboard collector

Add a focused power-file reader to the dashboard collector layer. It validates
the runtime file and returns a stable public power payload. `collect_status()`
adds the payload under the top-level `power` key and adds the sanitized error
`CPU package power unavailable` when necessary.

Power is informational. Missing or invalid power data does not change
`overall_status` and does not affect any existing metric.

### 5.3 Dashboard UI

Add one row to the existing CPU card:

```text
CPU Package 功耗    5.8 W（5秒平均·估算）
```

The row uses the existing status styling. Unavailable power is neutral gray and
reads `不可用`. No new card, graph, control, or historical view is added.

Change the browser polling interval from 10,000 ms to 5,000 ms. Failed requests
continue to retain the last successful full payload and retry after 5 seconds.

## 6. Sampler file contract

The sampler atomically writes `/run/n5105-dashboard/power.json`:

```json
{
  "version": 1,
  "source": "intel_rapl:package-0",
  "package_watts": 5.8,
  "sample_seconds": 5.0,
  "sampled_at": "2026-09-02T12:00:00Z"
}
```

Contract requirements:

- `version` is exactly `1`.
- `source` is exactly `intel_rapl:package-0`.
- `package_watts` is finite and non-negative.
- `sample_seconds` is finite, positive, and records the actual monotonic
  interval rather than assuming exactly 5 seconds.
- `sampled_at` is an RFC 3339 UTC timestamp ending in `Z`.
- The file is owned by `root:ubuntu` with mode `0640`.
- Publication writes a temporary file in the same directory, flushes it, and
  replaces the final path with `os.replace`; readers never observe partial
  JSON.

The sampler publishes its first file only after the first complete 5-second
sample. On a later sampler failure, it leaves the last good file in place. The
dashboard's staleness rule turns that sample unavailable without requiring the
root process to publish error details.

## 7. Public API contract

`/api/status` always contains a `power` object:

```json
{
  "package_watts": 5.8,
  "sample_seconds": 5.0,
  "status": "ok",
  "estimated": true
}
```

When unavailable:

```json
{
  "package_watts": null,
  "sample_seconds": null,
  "status": "unavailable",
  "estimated": true
}
```

The dashboard rejects the runtime sample and returns the unavailable payload
when the file is missing, unreadable, malformed, has invalid fields, has a
timestamp more than 5 seconds in the future, or is more than 15 seconds old.
Only the sanitized error string is exposed. The runtime path, JSON contents,
and exception messages never enter the API.

## 8. systemd and privilege boundary

Add `deploy/n5105-power-sampler.service` with these properties:

- `User=root` and `Group=ubuntu`.
- `ExecStart=/usr/bin/python3 /usr/local/libexec/n5105-power-sampler`.
- `RuntimeDirectory=n5105-dashboard` and `RuntimeDirectoryMode=0750`.
- `UMask=0027` so published files are `root:ubuntu 0640`.
- `Restart=on-failure` with a short restart delay.
- No network dependency and no listening socket.
- `NoNewPrivileges=true`, `PrivateTmp=true`, `PrivateDevices=true`,
  `ProtectSystem=strict`, `ProtectHome=true`, `ProtectKernelTunables=true`,
  `ProtectKernelModules=true`, and `ProtectControlGroups=true`.
- Write access limited to `/run/n5105-dashboard`; RAPL sysfs remains read-only.

Update `n5105-dashboard.service` with a soft ordering relationship:

- `Wants=n5105-power-sampler.service`
- `After=n5105-power-sampler.service`

Do not use `Requires=`. The dashboard must still start and serve all existing
metrics when the sampler is missing or failed.

## 9. Failure handling

- First five seconds after sampler start: power is unavailable; dashboard works.
- Missing RAPL nodes or wrong domain: sampler logs a local generic error and
  retries; dashboard shows unavailable.
- Counter wrap: corrected using the maximum energy range.
- Energy readings outside `0..max_energy_range_uj`, an elapsed interval that is
  not finite and positive, or a calculated result that is not finite and
  non-negative: discard the sample and retry. A lower second reading is treated
  as exactly one counter wrap; the sampler cannot infer multiple wraps.
- Runtime JSON missing, corrupt, unreadable, invalid, future-dated, or stale:
  return the stable unavailable API payload and sanitized error.
- Atomic publish failure: keep the previous file; it naturally becomes stale.
- Sampler service failure: systemd restarts only the sampler.
- Dashboard/API failure: existing last-good browser behavior remains intact.

## 10. Testing strategy

### Unit tests

- Normal 5-second energy-delta calculation.
- Actual elapsed-time use rather than a hard-coded divisor.
- RAPL counter wrap.
- Wrong domain, missing files, non-integer or out-of-range energy, zero/invalid
  maximum range, invalid elapsed time, and invalid calculated results.
- Atomic JSON publication and final file mode.
- Power JSON valid, missing, malformed, unreadable, non-finite, negative,
  future-dated, and older than 15 seconds.
- Power errors do not alter overall health and do not remove other metrics.
- Strict JSON serialization of the expanded payload.

### Route and UI tests

- `/api/status` exposes the stable `power` object.
- The CPU card renders watts, estimate label, and unavailable fallback.
- Polling and retry text use 5 seconds.
- Existing no-store, injection safety, and last-good-data behavior remain.

### Deployment tests

- The sampler unit executes only the root-owned installed script path.
- User/group, runtime directory, umask, restart policy, no network dependency,
  and hardening directives are exact.
- The dashboard unit has soft `Wants`/`After` ordering, not `Requires`.
- `systemd-analyze verify` passes on the Ubuntu target.

## 11. Deployment and verification

Deployment is a separate, explicitly authorized live step:

1. Back up the currently installed dashboard unit.
2. Install the sampler script as `root:root 0755` under `/usr/local/libexec`.
3. Install and validate the sampler unit.
4. Reload systemd and enable/start the sampler.
5. Wait for a complete sample and verify JSON freshness, ownership, mode, and
   changing energy-derived watts.
6. Install the updated dashboard unit, restart the dashboard, and verify
   `/healthz` and `/api/status` locally.
7. Verify the Cloudflare-protected page updates every 5 seconds and displays
   the estimate label.
8. Recheck `unionpay-rate.service`, `cloudflared.service`, and
   `rate.sonab.uk`; none are modified by this feature.

## 12. Rollback

If the dashboard change fails, restore the previous dashboard unit and code
first so the existing status page recovers. Then stop and disable the sampler,
restore or remove only the sampler's installed unit/script according to the
backups, run `daemon-reload`, and verify the original dashboard.

Rollback does not change Cloudflare DNS, Tunnel ingress, Access policies, the
UnionPay service, or the rate application.

## 13. Acceptance criteria

- The dashboard refreshes every 5 seconds.
- A healthy sampler produces a finite, non-negative CPU Package watt value
  averaged over the actual approximately 5-second interval.
- The UI labels the value as a 5-second estimate, not whole-system power.
- The FastAPI process remains unprivileged and cannot read RAPL directly.
- The root service executes only a root-owned installed script and exposes no
  network or command surface.
- Missing, stale, or corrupt power data affects only the power row.
- Existing metrics, services, Cloudflare protection, and UnionPay behavior are
  unchanged.
- Automated tests and live Ubuntu verification pass before activation.
