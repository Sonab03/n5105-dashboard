from datetime import datetime, timezone
import json
import stat

import pytest

from power_sampler import (
    build_sample,
    calculate_package_watts,
    publish_sample,
    read_rapl_value,
    validate_package_domain,
)


def test_calculate_package_watts_uses_actual_elapsed_time():
    assert calculate_package_watts(10_000_000, 16_000_000, 100_000_000, 3.0) == 2.0


def test_calculate_package_watts_corrects_one_counter_wrap():
    assert calculate_package_watts(98_000_000, 3_000_000, 100_000_000, 5.0) == 1.0


@pytest.mark.parametrize(
    "values",
    [
        (-1, 1, 100, 5.0),
        (1, 101, 100, 5.0),
        (1, 2, 0, 5.0),
        (1, 2, 100, 0.0),
        (1, 2, 100, float("nan")),
        (True, 2, 100, 5.0),
    ],
)
def test_calculate_package_watts_rejects_invalid_inputs(values):
    with pytest.raises(ValueError):
        calculate_package_watts(*values)


def test_read_rapl_value_accepts_only_decimal_integer(tmp_path):
    value_path = tmp_path / "energy_uj"
    value_path.write_text("12345\n", encoding="ascii")
    assert read_rapl_value(value_path) == 12345

    value_path.write_text("1.5\n", encoding="ascii")
    with pytest.raises(ValueError):
        read_rapl_value(value_path)


def test_validate_package_domain_rejects_any_other_domain(tmp_path):
    name_path = tmp_path / "name"
    name_path.write_text("package-0\n", encoding="ascii")
    validate_package_domain(name_path)

    name_path.write_text("core\n", encoding="ascii")
    with pytest.raises(ValueError):
        validate_package_domain(name_path)


def test_build_sample_has_stable_versioned_contract():
    sample = build_sample(
        5.8126,
        5.004,
        sampled_at=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
    )
    assert sample == {
        "version": 1,
        "source": "intel_rapl:package-0",
        "package_watts": 5.813,
        "sample_seconds": 5.004,
        "sampled_at": "2026-09-02T12:00:00Z",
    }


def test_publish_sample_atomically_writes_strict_json_with_mode_0640(tmp_path):
    target = tmp_path / "power.json"
    sample = build_sample(
        2.5,
        5.0,
        sampled_at=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
    )

    publish_sample(target, sample)

    assert json.loads(target.read_text(encoding="utf-8")) == sample
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert list(tmp_path.iterdir()) == [target]


def test_build_sample_rejects_nonfinite_or_invalid_values():
    for watts, seconds in ((float("inf"), 5.0), (-1.0, 5.0), (1.0, 0.0)):
        with pytest.raises(ValueError):
            build_sample(watts, seconds)
