import json
from copy import deepcopy
from datetime import UTC, datetime
from itertools import permutations
from pathlib import Path

import pytest

from scripts.import_rnmca import (
    missing_rnmca_hourly_intervals,
    normalize_rnmca_observations,
    reconcile_rnmca_data_elements,
    rnmca_hourly_is_eligible,
)

CAPTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "rnmca_dj1_no2.json").read_text()
)
CONTEXT = {
    "location_id": CAPTURE["locationId"],
    "measurement_value_id": CAPTURE["measurementValueId"],
    "ingested_at": datetime(2026, 10, 24, tzinfo=UTC),
}


def revision(version=10, value=12, **updates):
    row = deepcopy(CAPTURE["dataElement"])
    row["dataElementFields"].update(version=version, value=value, **updates)
    return row


def test_duplicates_collapse_and_replay_is_idempotent_without_mutating_input():
    row = revision()
    rows = [row, deepcopy(row)]
    original = deepcopy(rows)

    result = reconcile_rnmca_data_elements(rows, **CONTEXT)

    assert result == [row]
    assert reconcile_rnmca_data_elements(result + rows, **CONTEXT) == result
    result[0]["dataElementFields"]["value"] = 99
    assert rows == original


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_highest_version_wins_independently_of_arrival_order(order):
    rows = [revision(10, 50), revision(12, 0), revision(11, 70)]

    selected = reconcile_rnmca_data_elements([rows[i] for i in order], **CONTEXT)

    assert selected == [rows[1]]
    assert normalize_rnmca_observations(selected, **CONTEXT)[0]["value"] == 0


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("withdrawal", ["usable", "qa", "status", "null"])
def test_newer_rejection_replaces_accepted_data_and_leaves_a_gap(reverse, withdrawal):
    old = revision()
    new = revision(11)
    if withdrawal == "usable":
        new["usable"] = False
    elif withdrawal == "qa":
        new["dataElementFields"]["qaqcStatusId"] = 79002
    elif withdrawal == "status":
        new["dataElementFields"]["status"] = 1 << 1
    else:
        new["rangeExceedance"] = {"overrideValue": None}
    rows = [new, old] if reverse else [old, new]

    selected = reconcile_rnmca_data_elements(rows, **CONTEXT)
    accepted = normalize_rnmca_observations(selected, **CONTEXT)

    assert selected == [new]
    assert reconcile_rnmca_data_elements(selected + [old], **CONTEXT) == selected
    assert accepted == []
    assert missing_rnmca_hourly_intervals(
        accepted,
        window_start=datetime(2026, 10, 10, 18, tzinfo=UTC),
        window_end=datetime(2026, 10, 10, 19, tzinfo=UTC),
    ) == [
        {
            "observedFrom": "2026-10-10T18:00:00+00:00",
            "observedTo": "2026-10-10T19:00:00+00:00",
        }
    ]


def test_newer_accepted_revision_can_restore_a_rejected_interval():
    old = revision(10, qaqcStatusId=79002)
    new = revision(11, 42)

    selected = reconcile_rnmca_data_elements([new, old], **CONTEXT)

    assert normalize_rnmca_observations(selected, **CONTEXT)[0]["value"] == 42


@pytest.mark.parametrize("field", ["value", "qaqcStateId", "percentage"])
@pytest.mark.parametrize("reverse", [False, True])
def test_same_version_with_different_content_is_an_error_even_if_superseded(
    field, reverse
):
    first = revision()
    conflict = deepcopy(first)
    conflict["dataElementFields"][field] += 1
    rows = [first, conflict, revision(11)]
    if reverse:
        rows.reverse()

    with pytest.raises(ValueError, match="Conflicting RNMCA data"):
        reconcile_rnmca_data_elements(rows, **CONTEXT)


def test_identical_versions_on_different_intervals_remain_distinct_and_sorted():
    first = revision()
    second = deepcopy(first)
    second["timestamp"] += 3_600_000
    for key in ("minTimestamp", "maxTimestamp"):
        second["dataElementFields"][key] += 3_600_000

    assert reconcile_rnmca_data_elements([second, first], **CONTEXT) == [first, second]


@pytest.mark.parametrize("version", [None, True, -1, "11"])
def test_rejected_rows_still_require_valid_versions(version):
    row = revision(version)
    row["usable"] = False

    with pytest.raises(ValueError, match="version must be a nonnegative integer"):
        reconcile_rnmca_data_elements([row], **CONTEXT)


def test_rejected_rows_still_require_completed_intervals():
    row = revision()
    row["usable"] = False
    context = {**CONTEXT, "ingested_at": datetime(2026, 10, 1, tzinfo=UTC)}

    with pytest.raises(ValueError, match="requires completed hourly intervals"):
        reconcile_rnmca_data_elements([row], **context)


def test_reconciliation_rejects_nonhourly_rows():
    row = revision()
    row["dataElementFields"]["type"] = "Webs_FieldsDailyValue"

    with pytest.raises(ValueError, match="requires hourly data elements"):
        reconcile_rnmca_data_elements([row], **CONTEXT)


def test_reconciliation_rejects_unaligned_intervals():
    row = revision()
    row["timestamp"] += 1

    with pytest.raises(ValueError, match="must align to a UTC hour"):
        reconcile_rnmca_data_elements([row], **CONTEXT)


def test_unknown_quality_is_an_error_even_in_a_superseded_revision():
    old = revision(10, qaqcStatusId=99999)

    with pytest.raises(ValueError, match="Unknown RNMCA qaqcStatusId"):
        reconcile_rnmca_data_elements([revision(11), old], **CONTEXT)


def test_empty_input_returns_empty_list_but_checks_context():
    assert reconcile_rnmca_data_elements([], **CONTEXT) == []
    with pytest.raises(ValueError, match="supports only the captured"):
        reconcile_rnmca_data_elements([], **{**CONTEXT, "location_id": 0})
    with pytest.raises(ValueError, match="must include its UTC offset"):
        reconcile_rnmca_data_elements(
            [],
            **{**CONTEXT, "ingested_at": CONTEXT["ingested_at"].replace(tzinfo=None)},
        )


def test_rejected_row_with_invalid_timestamp_is_an_error():
    row = revision()
    row["usable"] = False
    row["timestamp"] = "invalid"

    with pytest.raises(TypeError, match="timestamp_ms must be an integer"):
        reconcile_rnmca_data_elements([row], **CONTEXT)


@pytest.mark.parametrize("rejection", ["unusable", "qa", "status"])
@pytest.mark.parametrize("malformed", ["boolean", "nonfinite", "missing", "override"])
@pytest.mark.parametrize("entry_point", ["predicate", "normalize", "reconcile"])
def test_known_rejection_does_not_hide_malformed_effective_values(
    rejection, malformed, entry_point
):
    row = revision()
    if rejection == "unusable":
        row["usable"] = False
    elif rejection == "qa":
        row["dataElementFields"]["qaqcStatusId"] = 79002
    else:
        row["dataElementFields"]["status"] = 1 << 19
    if malformed == "boolean":
        row["dataElementFields"]["value"] = True
        error, message = TypeError, "value must be a number or None"
    elif malformed == "nonfinite":
        row["dataElementFields"]["value"] = float("nan")
        error, message = ValueError, "value must be finite"
    elif malformed == "missing":
        row["rangeExceedance"] = {}
        error, message = ValueError, "Missing RNMCA overrideValue"
    else:
        row["rangeExceedance"] = []
        error, message = TypeError, "rangeExceedance must be an object"

    with pytest.raises(error, match=message):
        if entry_point == "predicate":
            rnmca_hourly_is_eligible(row)
        elif entry_point == "normalize":
            normalize_rnmca_observations([row], **CONTEXT)
        else:
            reconcile_rnmca_data_elements([row], **CONTEXT)


def test_same_version_different_json_types_in_provenance_are_conflicts():
    first = revision()
    first["providerMetadata"] = {"flag": True}
    second = deepcopy(first)
    second["providerMetadata"]["flag"] = 1

    with pytest.raises(ValueError, match="Conflicting RNMCA data"):
        reconcile_rnmca_data_elements([first, second], **CONTEXT)


def test_object_key_order_does_not_make_duplicate_content_conflicting():
    first = revision()
    second = dict(reversed(list(first.items())))
    second["dataElementFields"] = dict(
        reversed(list(first["dataElementFields"].items()))
    )

    assert reconcile_rnmca_data_elements([first, second], **CONTEXT) == [first]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_reconciliation_requires_json_safe_raw_provenance_even_with_valid_override(
    value,
):
    row = revision(value=value)
    row["rangeExceedance"] = {"overrideValue": 0}

    with pytest.raises(ValueError, match="Out of range float values"):
        reconcile_rnmca_data_elements([row], **CONTEXT)


def test_bounded_pipeline_reconciles_before_filtering_and_reports_edge_gaps():
    first = revision(10, 12)
    withdrawn = revision(11, 12, qaqcStatusId=79002)
    second = deepcopy(first)
    second["timestamp"] += 3_600_000
    for key in ("minTimestamp", "maxTimestamp"):
        second["dataElementFields"][key] += 3_600_000
    selected = reconcile_rnmca_data_elements(
        [second, first, withdrawn, second], **CONTEXT
    )
    accepted = normalize_rnmca_observations(selected, **CONTEXT)

    assert len(selected) == 2
    assert len(accepted) == 1
    assert accepted[0]["observedFrom"] == "2026-10-10T19:00:00+00:00"
    assert missing_rnmca_hourly_intervals(
        accepted,
        window_start=datetime(2026, 10, 10, 17, tzinfo=UTC),
        window_end=datetime(2026, 10, 10, 21, tzinfo=UTC),
    ) == [
        {
            "observedFrom": "2026-10-10T17:00:00+00:00",
            "observedTo": "2026-10-10T18:00:00+00:00",
        },
        {
            "observedFrom": "2026-10-10T18:00:00+00:00",
            "observedTo": "2026-10-10T19:00:00+00:00",
        },
        {
            "observedFrom": "2026-10-10T20:00:00+00:00",
            "observedTo": "2026-10-10T21:00:00+00:00",
        },
    ]
