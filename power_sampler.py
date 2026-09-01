from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


RAPL_ROOT = Path("/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0")
DOMAIN_PATH = RAPL_ROOT / "name"
ENERGY_PATH = RAPL_ROOT / "energy_uj"
MAX_ENERGY_PATH = RAPL_ROOT / "max_energy_range_uj"
OUTPUT_PATH = Path("/run/n5105-dashboard/power.json")
SAMPLE_INTERVAL_SECONDS = 5.0


def read_rapl_value(path: Path) -> int:
    raw = path.read_text(encoding="ascii").strip()
    if not raw.isdecimal():
        raise ValueError("invalid RAPL counter")
    return int(raw)


def validate_package_domain(path: Path = DOMAIN_PATH) -> None:
    if path.read_text(encoding="ascii").strip() != "package-0":
        raise ValueError("unexpected RAPL domain")


def calculate_package_watts(
    first_uj: int,
    second_uj: int,
    max_uj: int,
    elapsed_seconds: float,
) -> float:
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (first_uj, second_uj, max_uj)):
        raise ValueError("invalid RAPL counter type")
    if max_uj <= 0 or not 0 <= first_uj <= max_uj or not 0 <= second_uj <= max_uj:
        raise ValueError("RAPL counter outside range")
    if isinstance(elapsed_seconds, bool):
        raise ValueError("invalid sample interval")
    elapsed = float(elapsed_seconds)
    if not math.isfinite(elapsed) or elapsed <= 0:
        raise ValueError("invalid sample interval")
    delta_uj = (
        second_uj - first_uj
        if second_uj >= first_uj
        else max_uj - first_uj + second_uj
    )
    watts = delta_uj / 1_000_000 / elapsed
    if not math.isfinite(watts) or watts < 0:
        raise ValueError("invalid power result")
    return watts


def build_sample(
    package_watts: float,
    sample_seconds: float,
    *,
    sampled_at: datetime | None = None,
) -> dict:
    watts = float(package_watts)
    seconds = float(sample_seconds)
    if not math.isfinite(watts) or watts < 0:
        raise ValueError("invalid package power")
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("invalid sample interval")
    timestamp = sampled_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("sample timestamp must be timezone-aware")
    timestamp = timestamp.astimezone(timezone.utc).replace(microsecond=0)
    return {
        "version": 1,
        "source": "intel_rapl:package-0",
        "package_watts": round(watts, 3),
        "sample_seconds": round(seconds, 3),
        "sampled_at": timestamp.isoformat().replace("+00:00", "Z"),
    }


def publish_sample(path: Path, sample: dict) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            os.fchmod(output.fileno(), 0o640)
            json.dump(sample, output, allow_nan=False, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temporary_path), str(path))
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def sample_once() -> dict:
    validate_package_domain()
    maximum = read_rapl_value(MAX_ENERGY_PATH)
    first = read_rapl_value(ENERGY_PATH)
    started = time.monotonic()
    time.sleep(SAMPLE_INTERVAL_SECONDS)
    second = read_rapl_value(ENERGY_PATH)
    elapsed = time.monotonic() - started
    return build_sample(
        calculate_package_watts(first, second, maximum, elapsed), elapsed
    )


def main() -> None:
    while True:
        started = time.monotonic()
        try:
            publish_sample(OUTPUT_PATH, sample_once())
        except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as error:
            print(f"power sample unavailable: {type(error).__name__}", file=sys.stderr, flush=True)
            remaining = SAMPLE_INTERVAL_SECONDS - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)


if __name__ == "__main__":
    main()
