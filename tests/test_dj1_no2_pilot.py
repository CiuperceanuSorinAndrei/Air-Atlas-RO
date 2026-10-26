import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.dj1_no2_pilot import EEA_PILOT_RULE, reconcile_dj1_no2_window

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def eea():
    return json.loads((FIXTURES / "eea_dj1_no2.json").read_text())["eeaObservation"]


@pytest.fixture
def raw():
    return json.loads((FIXTURES / "rnmca_dj1_no2.json").read_text())["dataElement"]


def run(
    raw=(), eea=(), start="2026-10-10T18:00:00+00:00", end="2026-10-10T20:00:00+00:00"
):
    return reconcile_dj1_no2_window(
        raw,
        eea,
        window_start=datetime.fromisoformat(start),
        window_end=datetime.fromisoformat(end),
        ingested_at=datetime(2026, 10, 12, 9, tzinfo=UTC),
    )


def test_captured_overlap_prefers_rnmca_and_preserves_eea_provenance(raw, eea):
    before = deepcopy((raw, eea))
    report = run([raw], [eea])
    assert len(report["observations"]) == 1
    selected = report["observations"][0]
    assert selected["source"] == "RNMCA"
    assert selected["value"] == 48.81861538888885
    normalized = report["eeaObservations"][0]
    assert normalized["observedFrom"] == "2026-10-10T18:00:00+00:00"
    assert normalized["observedTo"] == "2026-10-10T19:00:00+00:00"
    assert normalized["normalizationRule"] == EEA_PILOT_RULE
    assert normalized["temporalStatus"] == "resolved"
    assert normalized["rawObservation"] == eea
    assert normalized["reportedAt"] == eea["reportedAt"]
    assert normalized["ingestedAt"] == eea["ingestedAt"]
    assert normalized["value"] == 48.81862
    assert report["latestObservation"] == selected
    assert len(report["missingIntervals"]) == 1
    assert (raw, eea) == before
    normalized["rawObservation"]["value"] = 0
    assert eea == before[1]


def test_eligible_eea_fills_missing_rnmca_hour(eea):
    result = run(eea=[eea])
    assert result["observations"][0]["source"] == "EEA"
    assert result["observations"][0]["observedTo"] == "2026-10-10T19:00:00+00:00"
    assert len(result["missingIntervals"]) == 1


def test_newer_eea_hour_is_latest_even_when_older_rnmca_exists(raw, eea):
    eea.update(
        observedFrom="2026-10-10T21:00:00+01:00", observedTo="2026-10-10T22:00:00+01:00"
    )
    report = run([raw], [eea])
    assert [row["source"] for row in report["observations"]] == ["RNMCA", "EEA"]
    assert report["latestObservation"]["source"] == "EEA"
    assert report["missingIntervals"] == []


def test_rejected_rnmca_allows_independently_eligible_eea(raw, eea):
    raw["dataElementFields"]["version"] += 1
    raw["usable"] = False
    report = run([raw], [eea])
    assert report["currentRnmcaRevisions"][0]["usable"] is False
    assert report["observations"][0]["source"] == "EEA"


def test_withdrawn_rnmca_cannot_be_revived_by_old_revision(raw):
    newer = deepcopy(raw)
    newer["dataElementFields"]["version"] += 1
    newer["usable"] = False
    report = run([newer, raw])
    assert report["observations"] == []
    assert len(report["missingIntervals"]) == 2


def test_overlap_disagreement_quarantines_eea_without_removing_rnmca(raw, eea):
    eea["value"] += 1
    report = run([raw], [eea])
    assert report["observations"][0]["source"] == "RNMCA"
    assert report["excludedEeaObservations"][0]["temporalStatus"] == "conflict"
    assert report["excludedEeaObservations"][0]["observedTo"] is None


@pytest.mark.parametrize("delta", [0.000009, -0.000009])
def test_source_precision_tolerance_is_absolute(raw, eea, delta):
    eea["value"] = raw["dataElementFields"]["value"] + delta
    assert run([raw], [eea])["excludedEeaObservations"] == []


def test_zero_eea_value_remains_an_eligible_measurement(eea):
    eea["value"] = 0
    assert run(eea=[eea])["observations"][0]["value"] == 0


def test_known_eea_rejection_leaves_a_gap(eea):
    eea["validity"] = -1
    report = run(eea=[eea])
    assert report["observations"] == []
    assert report["excludedEeaObservations"][0]["temporalStatus"] == "qa-rejected"


def test_newer_eea_rejection_does_not_revive_old_accepted_revision(eea):
    newer = dict(eea, validity=-1, reportedAt="2026-10-11T12:00:00+01:00")
    report = run(eea=[newer, eea])
    assert report["observations"] == []
    assert len(report["eeaObservations"]) == 1


def test_newest_eea_correction_wins_independently_of_order(eea):
    newer = dict(eea, value=51, reportedAt="2026-10-11T12:00:00+01:00")
    a = run(eea=[newer, eea, deepcopy(newer)])
    b = run(eea=[eea, newer])
    assert a == b
    assert a["observations"][0]["value"] == 51


def test_identical_eea_content_reingested_later_is_deduplicated(eea):
    later = dict(eea, ingestedAt="2026-10-12T08:45:00+00:00")
    assert run(eea=[eea, later]) == run(eea=[later, eea])
    assert len(run(eea=[eea, later])["observations"]) == 1


def test_conflicting_equal_eea_report_times_are_rejected(eea):
    with pytest.raises(ValueError, match="Conflicting EEA data"):
        run(eea=[eea, dict(eea, value=99)])


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("stationId", "RO0008R"),
        ("samplingPointId", "RO/SPO-RO0078A_00007_100"),
        ("pollutant", "O3"),
        ("source", "RNMCA"),
        ("unit", "mg.m-3"),
        ("aggregationType", "day"),
        ("validity", True),
        ("validity", -99),
        ("verification", 0),
        ("status", "validated"),
        ("value", True),
        ("value", float("nan")),
        ("value", float("inf")),
        ("sourceUrl", "https://example.com/SPO-RO0078A_00008_100.parquet"),
        (
            "sourceUrl",
            "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0078A_00008_101.parquet",
        ),
        ("observedFrom", "2026-10-10T20:00:00"),
        ("observedFrom", "2026-10-10T20:00:00+00:00"),
        ("observedTo", "2026-10-10T21:30:00+01:00"),
        ("reportedAt", "2026-10-13T00:00:00+01:00"),
    ],
)
def test_malformed_or_out_of_scope_eea_is_rejected(eea, key, value):
    eea[key] = value
    with pytest.raises((ValueError, TypeError)):
        run(eea=[eea])


def test_rejected_qa_cannot_hide_malformed_value(eea):
    eea.update(validity=-1, value="invalid")
    with pytest.raises(TypeError):
        run(eea=[eea])


@pytest.mark.parametrize("value", [None, [], "row", True])
def test_non_object_eea_rows_are_rejected(value):
    with pytest.raises(TypeError):
        run(eea=[value])


def test_empty_inputs_report_all_explicit_window_hours():
    report = run()
    assert report["observations"] == []
    assert report["latestObservation"] is None
    assert len(report["missingIntervals"]) == 2


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-10T18:00:00+00:00", "2026-10-10T18:00:00+00:00"),
        ("2026-10-10T18:30:00+00:00", "2026-10-10T20:00:00+00:00"),
        ("2026-10-10T18:00:00", "2026-10-10T20:00:00+00:00"),
        ("2024-12-31T23:00:00+00:00", "2025-01-01T01:00:00+00:00"),
        ("2026-10-12T08:00:00+00:00", "2026-10-12T09:00:00+00:00"),
    ],
)
def test_window_contract_is_checked_even_when_empty(start, end):
    with pytest.raises(ValueError):
        run(start=start, end=end)


def test_rejected_rnmca_outside_window_is_still_rejected(raw):
    raw["usable"] = False
    with pytest.raises(ValueError, match="outside the requested window"):
        run([raw], start="2026-10-10T19:00:00+00:00")


def test_eea_outside_window_is_rejected(eea):
    with pytest.raises(ValueError, match="outside the requested window"):
        run(eea=[eea], start="2026-10-10T19:00:00+00:00")


def test_eea_ingestion_after_replay_is_rejected(eea):
    eea["ingestedAt"] = "2026-10-12T10:00:00+00:00"
    with pytest.raises(ValueError, match="after pilot replay"):
        run(eea=[eea])


def seasonal_row(eea, start, end):
    return dict(
        eea, observedFrom=start, observedTo=end, reportedAt="2026-10-11T22:22:32+01:00"
    )


def test_winter_eea_is_not_shifted_by_one_hour(eea):
    row = seasonal_row(eea, "2026-01-15T20:00:00+01:00", "2026-01-15T21:00:00+01:00")
    report = run(
        eea=[row], start="2026-01-15T19:00:00+00:00", end="2026-01-15T20:00:00+00:00"
    )
    assert report["observations"][0]["observedTo"] == "2026-01-15T20:00:00+00:00"


def test_nonexistent_spring_end_is_not_invented(eea):
    row = seasonal_row(eea, "2026-03-29T01:00:00+01:00", "2026-03-29T02:00:00+01:00")
    report = run(
        eea=[row], start="2026-03-29T00:00:00+00:00", end="2026-03-29T03:00:00+00:00"
    )
    assert report["observations"] == []
    assert report["excludedEeaObservations"][0]["temporalStatus"] == "nonexistent"


def test_spring_interval_is_one_physical_hour_across_clock_jump(eea):
    row = seasonal_row(eea, "2026-03-29T02:00:00+01:00", "2026-03-29T03:00:00+01:00")
    report = run(
        eea=[row], start="2026-03-29T00:00:00+00:00", end="2026-03-29T01:00:00+00:00"
    )
    result = report["observations"][0]
    assert result["observedFrom"] == "2026-03-29T00:00:00+00:00"
    assert result["observedTo"] == "2026-03-29T01:00:00+00:00"


def test_autumn_ambiguous_end_is_excluded_without_reference(eea):
    row = seasonal_row(eea, "2025-10-26T01:00:00+01:00", "2025-10-26T02:00:00+01:00")
    report = run(
        eea=[row], start="2025-10-25T23:00:00+00:00", end="2025-10-26T02:00:00+00:00"
    )
    assert report["observations"] == []
    excluded = report["excludedEeaObservations"][0]
    assert excluded["temporalStatus"] == "ambiguous"
    assert len(excluded["candidateIntervals"]) == 2


def autumn_raw(raw, end, value):
    row = deepcopy(raw)
    row["timestamp"] = int(datetime.fromisoformat(end).timestamp()) * 1000
    row["dataElementFields"].update(value=value, minTimestamp=None, maxTimestamp=None)
    return row


def test_one_matching_value_cannot_resolve_an_autumn_label(raw, eea):
    row = seasonal_row(eea, "2025-10-26T01:00:00+01:00", "2025-10-26T02:00:00+01:00")
    reference = autumn_raw(raw, "2025-10-26T01:00:00+00:00", row["value"])
    report = run(
        [reference],
        [row],
        start="2025-10-25T23:00:00+00:00",
        end="2025-10-26T02:00:00+00:00",
    )
    assert report["eeaObservations"][0]["temporalStatus"] == "ambiguous"
    assert report["eeaObservations"][0]["observedTo"] is None
    assert report["observations"][0]["source"] == "RNMCA"


def test_equal_values_in_both_repeated_hours_do_not_resolve_ambiguity(raw, eea):
    row = seasonal_row(eea, "2025-10-26T01:00:00+01:00", "2025-10-26T02:00:00+01:00")
    refs = [
        autumn_raw(raw, end, row["value"])
        for end in ["2025-10-26T00:00:00+00:00", "2025-10-26T01:00:00+00:00"]
    ]
    report = run(
        refs, [row], start="2025-10-25T23:00:00+00:00", end="2025-10-26T02:00:00+00:00"
    )
    assert report["excludedEeaObservations"][0]["temporalStatus"] == "ambiguous"
    assert len(report["observations"]) == 2


def test_23_and_25_hour_windows_keep_physical_coverage():
    for start, end, hours in [
        ("2026-03-28T22:00:00+00:00", "2026-03-29T21:00:00+00:00", 23),
        ("2025-10-25T21:00:00+00:00", "2025-10-26T22:00:00+00:00", 25),
    ]:
        assert len(run(start=start, end=end)["missingIntervals"]) == hours


def test_repaired_export_labels_are_quarantined_instead_of_shifted_again(raw, eea):
    eea.update(
        observedFrom="2026-10-10T19:00:00+01:00", observedTo="2026-10-10T20:00:00+01:00"
    )
    report = run([raw], [eea], start="2026-10-10T17:00:00+00:00")
    assert len(report["observations"]) == 1
    assert report["observations"][0]["source"] == "RNMCA"
    assert report["excludedEeaObservations"][0]["temporalStatus"] == "conflict"


def test_ordinary_label_with_fold_one_still_has_one_candidate():
    from scripts.observation_time import bucharest_utc_candidates

    label = datetime.fromisoformat("2026-07-15T22:00:00").replace(fold=1)
    assert bucharest_utc_candidates(label) == (datetime(2026, 7, 15, 19, tzinfo=UTC),)


def test_full_pipeline_is_order_independent_and_inputs_detached(raw, eea):
    later = dict(
        eea,
        observedFrom="2026-10-10T21:00:00+01:00",
        observedTo="2026-10-10T22:00:00+01:00",
    )
    before = deepcopy((raw, eea, later))
    first = run([raw, deepcopy(raw)], [later, eea, deepcopy(eea)])
    second = run([raw], [eea, later])
    assert first == second
    assert (raw, eea, later) == before
    first["observations"][0]["rawObservation"]["usable"] = False
    assert raw == before[0]


def test_conflicting_rnmca_revision_aborts_combined_pipeline(raw, eea):
    conflict = deepcopy(raw)
    conflict["dataElementFields"]["value"] += 1
    with pytest.raises(ValueError, match="Conflicting RNMCA"):
        run([raw, conflict], [eea])


def test_unknown_rnmca_qa_cannot_be_hidden_by_eea_fallback(raw, eea):
    raw["dataElementFields"]["qaqcStatusId"] = -99
    with pytest.raises(ValueError):
        run([raw], [eea])


def test_successful_eea_revision_does_not_mutate_previous_capture(eea):
    original = deepcopy(eea)
    report = run(eea=[eea])
    selected = report["observations"][0]
    assert selected["rawObservation"]["observedFrom"] == original["observedFrom"]
    assert selected["rawObservation"]["observedTo"] == original["observedTo"]
    assert selected["observedFrom"] != original["observedFrom"]
    assert eea == original


def test_no_fallback_from_ambiguous_hour_even_when_other_hour_is_missing(raw, eea):
    row = seasonal_row(eea, "2025-10-26T01:00:00+01:00", "2025-10-26T02:00:00+01:00")
    report = run(
        eea=[row], start="2025-10-26T00:00:00+00:00", end="2025-10-26T01:00:00+00:00"
    )
    assert report["observations"] == []
    assert len(report["missingIntervals"]) == 1
    assert report["excludedEeaObservations"][0]["temporalStatus"] == "ambiguous"


def test_eea_qa_rejection_does_not_hide_conflicting_duplicate_content(eea):
    a = dict(eea, validity=-1)
    b = dict(a, value=99)
    with pytest.raises(ValueError, match="Conflicting EEA"):
        run(eea=[a, b])


def test_completed_hour_contract_rejects_in_progress_eea(eea):
    row = dict(
        eea,
        observedFrom="2026-01-15T20:00:00+01:00",
        observedTo="2026-01-15T21:00:00+01:00",
        reportedAt="2026-01-15T19:15:00+00:00",
        ingestedAt="2026-01-15T19:30:00+00:00",
    )
    with pytest.raises(ValueError, match="completed observation interval"):
        run(
            eea=[row],
            start="2026-01-15T19:00:00+00:00",
            end="2026-01-15T20:00:00+00:00",
        )
