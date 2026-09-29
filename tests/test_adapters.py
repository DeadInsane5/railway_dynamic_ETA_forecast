"""T02 done-checks for app.adapters (mock NTES/COA/RTIS feeds)."""

import csv

import pytest

from app.adapters import (
    COAAdapter,
    NTESAdapter,
    RTISAdapter,
    RealAdapter,
    poll_all,
)

EXPECTED_KEYS = {
    "train_id",
    "ts",
    "station",
    "section",
    "delay_min",
    "speed_kmph",
    "state",
    "source",
    "remark",
}


def _raw_rows():
    with open("data/events.csv", newline="") as f:
        return list(csv.DictReader(f))


def test_poll_all_600_has_all_three_sources():
    records = poll_all(600)
    assert len(records) > 0
    assert {r["source"] for r in records} == {"NTES", "COA", "RTIS"}
    for r in records:
        assert set(r.keys()) == EXPECTED_KEYS
        assert r["state"] in {"running", "halted", "at_station"}


def test_ntes_rows_have_speed_none():
    records = NTESAdapter().poll(600)
    assert len(records) > 0
    assert all(r["source"] == "NTES" for r in records)
    assert all(r["speed_kmph"] is None for r in records)
    assert all(r["remark"] == "" for r in records)


def test_coa_rows_carry_remark_and_no_speed():
    records = COAAdapter().poll(600)
    assert len(records) > 0
    assert all(r["source"] == "COA" for r in records)
    assert all(r["speed_kmph"] is None for r in records)
    # Remark passthrough: replay far enough to include a remark-bearing row.
    late = COAAdapter().poll(800)
    remarked = [r for r in late if r["remark"]]
    assert len(remarked) > 0


def test_rtis_rows_carry_speed():
    records = RTISAdapter().poll(600)
    assert len(records) > 0
    assert all(r["source"] == "RTIS" for r in records)
    assert all(r["remark"] == "" for r in records)
    with_speed = [r for r in records if r["speed_kmph"] is not None]
    assert len(with_speed) > 0
    assert all(isinstance(r["speed_kmph"], float) for r in with_speed)


def test_unparseable_rows_are_skipped_but_bad_values_pass_through():
    records = poll_all(600)
    for r in records:
        assert r["train_id"].strip()
        assert r["station"].strip()
        assert isinstance(r["ts"], float)
        assert isinstance(r["delay_min"], float)
    # The 3 blank demo rows (T106/T112/T116 with empty ts/station/delay) are skipped.
    raw = _raw_rows()
    blank_ids = {
        row["train_id"]
        for row in raw
        if not (row.get("ts") or "").strip() or not (row.get("station") or "").strip()
    }
    assert blank_ids  # sanity: bad demo rows exist in the fixture
    for r in records:
        row = next(
            (
                x
                for x in raw
                if x["train_id"] == r["train_id"]
                and (x["ts"] or "").strip() == str(r["ts"])
            ),
            None,
        )
        assert row is not None
    # Out-of-range delay (-45.0 at ts 560.8) passes through: value validation is T03/T04's job.
    assert any(
        r["delay_min"] == -45.0 and r["ts"] == 560.8 for r in NTESAdapter().poll(600)
    )


def test_future_rows_not_exposed():
    for adapter, lag in ((NTESAdapter(), 5.0), (COAAdapter(), 5.0), (RTISAdapter(), 1.0)):
        for r in adapter.poll(600):
            assert r["ts"] <= 600 - lag
    # A row known to be in the future at now=600 (ts=738.2 remark row) is absent.
    assert all(r["ts"] != pytest.approx(738.2) for r in poll_all(600))
    assert any(r["remark"] for r in COAAdapter().poll(800))  # visible later


def test_lag_difference_between_feeds():
    ntes_ts = {r["ts"] for r in NTESAdapter().poll(600)}
    rtis_ts = {r["ts"] for r in RTISAdapter().poll(600)}
    assert ntes_ts <= rtis_ts  # 1-min-lag feed sees rows the 5-min-lag feed cannot yet
    assert rtis_ts - ntes_ts  # strictly more (rows with 595 < ts <= 599)


def test_real_adapter_stub():
    with pytest.raises(NotImplementedError):
        RealAdapter().poll(600)
