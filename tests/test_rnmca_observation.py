import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.import_rnmca import (
    RNMCA_INVALID_STATUS_MASK,
    RNMCA_KNOWN_STATUS_MASK,
    normalize_rnmca_observation,
    rnmca_effective_value,
    rnmca_hourly_is_eligible,
)

CAPTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "rnmca_dj1_no2.json").read_text(
        encoding="utf-8"
    )
)
INGESTED_AT = datetime(2026, 10, 22, 8, tzinfo=UTC)


@pytest.fixture
def row():
    return deepcopy(CAPTURE["dataElement"])


def normalize(row, **overrides):
    arguments = {
        "location_id": CAPTURE["locationId"],
        "measurement_value_id": CAPTURE["measurementValueId"],
        "ingested_at": INGESTED_AT,
    }
    arguments.update(overrides)
    return normalize_rnmca_observation(row, **arguments)


@pytest.mark.parametrize("state", [78001, 78002, 78003])
@pytest.mark.parametrize("status", [79001, 79002, 79003])
@pytest.mark.parametrize("usable", [False, True])
def test_hourly_eligibility_respects_native_quality(row, state, status, usable):
    row["usable"] = usable
    row["dataElementFields"].update(qaqcStateId=state, qaqcStatusId=status)

    assert rnmca_hourly_is_eligible(row) is (usable and status == 79001)


def test_status_masks_match_captured_channel_configuration():
    entries = CAPTURE["statusGroup"]["statusGroupEntries"]

    assert CAPTURE["statusGroup"]["id"] == 95002
    assert RNMCA_KNOWN_STATUS_MASK == sum(1 << entry["position"] for entry in entries)
    assert RNMCA_INVALID_STATUS_MASK == sum(
        1 << entry["position"] for entry in entries if entry["invalidatesValue"]
    )


def test_pilot_identity_unit_and_hourly_type_match_captured_configuration():
    assert CAPTURE["measurementValue"]["id"] == 6404090401
    assert CAPTURE["measurementValue"]["statusGroup"] == 95002
    assert CAPTURE["measurementValue"]["valueSubtype"] == CAPTURE["valueSubtype"]["id"]
    assert CAPTURE["valueSubtype"]["id"] == 4001
    assert CAPTURE["measurementValue"]["parameterMeasurementUnit"] == 320401
    assert (
        CAPTURE["parameterMeasurementUnit"]["parameter"] == CAPTURE["parameter"]["id"]
    )
    assert CAPTURE["parameter"]["code"] == "NO2"
    assert CAPTURE["parameterMeasurementUnit"]["measurementUnit"] == 30001
    assert CAPTURE["measurementUnit"]["symbol"] == "µg/m³"


@pytest.mark.parametrize(
    "entry",
    CAPTURE["statusGroup"]["statusGroupEntries"],
    ids=lambda entry: str(entry["status"]),
)
def test_each_captured_status_flag_respects_its_invalidation_rule(row, entry):
    row["dataElementFields"]["status"] = 1 << entry["position"]

    assert rnmca_hourly_is_eligible(row) is (not entry["invalidatesValue"])


@pytest.mark.parametrize("key", ["qaqcStateId", "qaqcStatusId"])
@pytest.mark.parametrize("value", [None, True, "79001", 99999])
def test_unknown_or_malformed_qa_is_not_silently_accepted(row, key, value):
    row["usable"] = False
    row["dataElementFields"][key] = value

    with pytest.raises(ValueError if type(value) is int else TypeError):
        rnmca_hourly_is_eligible(row)


@pytest.mark.parametrize("status", [-1, 1 << 27, (1 << 27) | 1])
def test_unknown_status_flags_are_explicit_errors(row, status):
    row["dataElementFields"]["status"] = status

    with pytest.raises(ValueError, match="Unknown RNMCA status flags"):
        rnmca_hourly_is_eligible(row)


@pytest.mark.parametrize("value", [1, "true", None])
def test_usable_requires_an_actual_boolean(row, value):
    row["usable"] = value

    with pytest.raises(TypeError, match="usable"):
        rnmca_hourly_is_eligible(row)


@pytest.mark.parametrize(
    "field_type", ["Webs_FieldsDailyValue", "Webs_FieldsMobileValue"]
)
def test_non_hourly_aggregations_do_not_enter_the_pilot(row, field_type):
    row["dataElementFields"]["type"] = field_type

    assert rnmca_hourly_is_eligible(row) is False
    assert normalize(row) is None


@pytest.mark.parametrize("field_type", [None, True, 42, "UNKNOWN"])
def test_unknown_or_malformed_field_types_are_explicit_errors(row, field_type):
    row["dataElementFields"]["type"] = field_type

    with pytest.raises(ValueError if isinstance(field_type, str) else TypeError):
        normalize(row)


@pytest.mark.parametrize("use_override", [False, True])
def test_unavailable_effective_value_is_not_an_eligible_observation(row, use_override):
    if use_override:
        row["rangeExceedance"] = {"overrideValue": None}
    else:
        row["dataElementFields"]["value"] = None

    assert rnmca_hourly_is_eligible(row) is False
    assert normalize(row) is None


@pytest.mark.parametrize(
    "rejection", ["unusable", "incorrect", "uncertain", "invalid-flag"]
)
def test_quality_rejections_do_not_produce_accepted_observations(row, rejection):
    if rejection == "unusable":
        row["usable"] = False
    elif rejection == "incorrect":
        row["dataElementFields"]["qaqcStatusId"] = 79002
    elif rejection == "uncertain":
        row["dataElementFields"]["qaqcStatusId"] = 79003
    else:
        row["dataElementFields"]["status"] = 1 << 19

    assert normalize(row) is None


def test_captured_observation_is_assembled_with_provenance_and_unknown_reporting_time(
    row,
):
    original = deepcopy(row)

    observation = normalize(row)

    assert observation == {
        "source": "RNMCA",
        "stationCode": "DJ-1",
        "sourceLocationId": 27064,
        "sourceMeasurementValueId": 6404090401,
        "sourceVersion": 3613450,
        "pollutant": "NO2",
        "unit": "ug.m-3",
        "aggregationType": "hour",
        "value": 48.81861538888885,
        "observedFrom": "2026-10-10T18:00:00+00:00",
        "observedTo": "2026-10-10T19:00:00+00:00",
        "reportedAt": None,
        "ingestedAt": "2026-10-22T08:00:00+00:00",
        "qaState": "validated",
        "qaqcStateId": 78002,
        "qaqcStatusId": 79001,
        "sourceStatus": 0,
        "normalizationRule": "rnmca-dj1-no2-hourly-epoch-utc-v1",
        "temporalStatus": "resolved",
        "rawObservation": original,
    }
    assert row == original
    row["dataElementFields"]["value"] = 999
    assert observation["rawObservation"] == original
    assert json.loads(json.dumps(observation, allow_nan=False))["reportedAt"] is None


@pytest.mark.parametrize(
    ("state", "label"), [(78001, "raw"), (78002, "validated"), (78003, "certified")]
)
def test_qa_stage_is_retained_without_promoting_raw_data(row, state, label):
    row["dataElementFields"]["qaqcStateId"] = state

    assert normalize(row)["qaState"] == label


def test_zero_override_survives_assembly_without_overwriting_original_value(row):
    row["rangeExceedance"] = {"overrideValue": 0}

    observation = normalize(row)

    assert observation["value"] == 0
    assert (
        observation["rawObservation"]["dataElementFields"]["value"] == 48.81861538888885
    )


@pytest.mark.parametrize(
    ("argument", "value"),
    [("location_id", 27065), ("measurement_value_id", 6404090402)],
)
def test_other_channels_cannot_be_mislabeled_as_dj1_no2(row, argument, value):
    with pytest.raises(ValueError, match="only the captured DJ-1 / NO2 channel"):
        normalize(row, **{argument: value})


@pytest.mark.parametrize("argument", ["location_id", "measurement_value_id"])
def test_channel_ids_cannot_be_booleans(row, argument):
    with pytest.raises(TypeError, match="IDs must be integers"):
        normalize(row, **{argument: True})


def test_ingestion_is_normalized_to_utc_and_remains_distinct_from_observation(row):
    ingestion = datetime.fromisoformat("2026-10-22T11:00:00+03:00")

    assert (
        normalize(row, ingested_at=ingestion)["ingestedAt"]
        == "2026-10-22T08:00:00+00:00"
    )


def test_timezone_naive_ingestion_is_rejected(row):
    with pytest.raises(ValueError, match="UTC offset"):
        normalize(row, ingested_at=INGESTED_AT.replace(tzinfo=None))


def test_in_progress_interval_is_excluded_from_completed_hour_pilot(row):
    with pytest.raises(ValueError, match="completed hourly"):
        normalize(row, ingested_at=datetime(2026, 10, 10, 18, 30, tzinfo=UTC))


def test_interval_ending_at_ingestion_is_completed(row):
    observation = normalize(row, ingested_at=datetime(2026, 10, 10, 19, tzinfo=UTC))

    assert observation["observedTo"] == observation["ingestedAt"]


@pytest.mark.parametrize("key", ["minTimestamp", "maxTimestamp"])
@pytest.mark.parametrize("timestamp", [1791655199999, 1791658800001])
def test_extrema_outside_the_interval_are_temporal_errors(row, key, timestamp):
    row["dataElementFields"][key] = timestamp

    with pytest.raises(ValueError, match="outside the observation interval"):
        normalize(row)


def test_unreported_extrema_remain_unknown_in_the_original(row):
    row["dataElementFields"]["minTimestamp"] = None
    del row["dataElementFields"]["maxTimestamp"]

    observation = normalize(row)

    assert observation["rawObservation"]["dataElementFields"]["minTimestamp"] is None
    assert "maxTimestamp" not in observation["rawObservation"]["dataElementFields"]


@pytest.mark.parametrize("version", [None, True, -1, "3613450"])
def test_source_version_is_required_and_preserved_as_an_integer(row, version):
    row["dataElementFields"]["version"] = version

    with pytest.raises(ValueError, match="version"):
        normalize(row)


@pytest.mark.parametrize("timestamp", [None, True, "1791658800000"])
def test_source_epoch_requires_an_integer(row, timestamp):
    row["timestamp"] = timestamp

    with pytest.raises(TypeError, match="timestamp_ms"):
        normalize(row)


@pytest.mark.parametrize(
    ("timestamp", "start", "end"),
    [
        (1774746000000, "2026-03-29T00:00:00+00:00", "2026-03-29T01:00:00+00:00"),
        (1761436800000, "2025-10-25T23:00:00+00:00", "2025-10-26T00:00:00+00:00"),
        (1761440400000, "2025-10-26T00:00:00+00:00", "2025-10-26T01:00:00+00:00"),
    ],
)
def test_assembly_retains_distinct_utc_intervals_across_dst(row, timestamp, start, end):
    row["timestamp"] = timestamp
    row["dataElementFields"]["minTimestamp"] = timestamp - 110_000
    row["dataElementFields"]["maxTimestamp"] = timestamp - 2_160_000

    observation = normalize(row)

    assert (observation["observedFrom"], observation["observedTo"]) == (start, end)
    assert observation["rawObservation"]["timestamp"] == timestamp


@pytest.mark.parametrize("override", [None, [], "override"])
def test_malformed_override_object_is_an_explicit_type_error(row, override):
    row["rangeExceedance"] = override

    with pytest.raises(TypeError, match="rangeExceedance"):
        rnmca_effective_value(row)


def test_override_can_supply_a_value_when_the_ordinary_value_is_absent(row):
    del row["dataElementFields"]["value"]
    row["rangeExceedance"] = {"overrideValue": 0}

    assert normalize(row)["value"] == 0


@pytest.mark.parametrize("malformed", [None, [], True])
def test_malformed_data_elements_are_explicit_errors(malformed):
    with pytest.raises(TypeError, match="data element"):
        rnmca_hourly_is_eligible(malformed)


def test_ordinary_missing_value_is_an_explicit_error(row):
    del row["dataElementFields"]["value"]

    with pytest.raises(ValueError, match="Missing RNMCA value"):
        rnmca_effective_value(row)
