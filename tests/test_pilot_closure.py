import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.dj1_no2_pilot import reconcile_dj1_no2_window
from scripts.import_rnmca import normalize_rnmca_observation

FIXTURES = Path(__file__).parent / "fixtures"
INGESTED = datetime(2026, 10, 12, 9, tzinfo=UTC)


@pytest.fixture
def eea():
    return json.loads((FIXTURES / "eea_dj1_no2.json").read_text())["eeaObservation"]


@pytest.fixture
def raw():
    return json.loads((FIXTURES / "rnmca_dj1_no2.json").read_text())["dataElement"]


def replay(eea=(), ingested_at=INGESTED):
    return reconcile_dj1_no2_window(
        [],
        eea,
        window_start=datetime(2026, 10, 10, 18, tzinfo=UTC),
        window_end=datetime(2026, 10, 10, 20, tzinfo=UTC),
        ingested_at=ingested_at,
    )


@pytest.mark.parametrize("order", [False, True])
def test_superseded_eea_cannot_hide_ingestion_after_replay(eea, order):
    invalid = dict(eea, ingestedAt="2026-10-13T00:00:00+00:00")
    newer = dict(eea, reportedAt="2026-10-11T23:22:32+01:00")
    rows = [invalid, newer] if order else [newer, invalid]
    with pytest.raises(ValueError, match="after pilot replay"):
        replay(rows)


def test_empty_future_window_cannot_be_reported_as_missing():
    with pytest.raises(ValueError, match="window_end must not be after ingestion"):
        replay(ingested_at=datetime(2026, 10, 10, 19, tzinfo=UTC))


@pytest.mark.parametrize("superseded", [False, True])
def test_null_value_on_known_eea_invalidation_is_preserved(eea, superseded):
    invalid = dict(eea, validity=-1, value=None, reportedAt="2026-10-11T23:22:32+01:00")
    before = deepcopy(invalid)
    report = replay([eea, invalid] if superseded else [invalid])
    assert report["observations"] == []
    assert report["excludedEeaObservations"][0]["temporalStatus"] == "qa-rejected"
    assert report["excludedEeaObservations"][0]["rawObservation"]["value"] is None
    assert invalid == before


@pytest.mark.parametrize("value", ["bad", True, float("nan"), float("inf")])
def test_known_eea_invalidation_still_rejects_malformed_values(eea, value):
    with pytest.raises((TypeError, ValueError)):
        replay([dict(eea, validity=-1, value=value)])


@pytest.mark.parametrize(
    "invalid",
    [None, "2026-10-12T09:00:00Z", datetime.fromisoformat("2026-10-12T09:00:00")],
)
def test_invalid_replay_context_is_rejected_even_without_inputs(invalid):
    with pytest.raises((TypeError, ValueError)):
        replay(ingested_at=invalid)


@pytest.mark.parametrize("rejection", ["usable", "qa", "null", "flag"])
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("version", -1),
        ("version", True),
        ("minTimestamp", True),
        ("maxTimestamp", 1791658800001),
    ],
)
def test_single_row_qa_rejection_cannot_hide_malformed_revision(
    raw, rejection, key, value
):
    if rejection == "usable":
        raw["usable"] = False
    elif rejection == "qa":
        raw["dataElementFields"]["qaqcStatusId"] = 79002
    elif rejection == "null":
        raw["dataElementFields"]["value"] = None
    else:
        raw["dataElementFields"]["status"] = 1 << 1
    raw["dataElementFields"][key] = value
    with pytest.raises((TypeError, ValueError)):
        normalize_rnmca_observation(
            raw,
            location_id=27064,
            measurement_value_id=6404090401,
            ingested_at=INGESTED,
        )


@pytest.mark.parametrize("timestamp", [None, True, "1791658800000", 1791802800000])
def test_single_row_rejected_observation_still_validates_interval(raw, timestamp):
    raw["usable"] = False
    raw["timestamp"] = timestamp
    with pytest.raises((TypeError, ValueError)):
        normalize_rnmca_observation(
            raw,
            location_id=27064,
            measurement_value_id=6404090401,
            ingested_at=INGESTED,
        )


def test_window_offsets_are_compared_as_instants(eea):
    report = reconcile_dj1_no2_window(
        [],
        [eea],
        window_start=datetime.fromisoformat("2026-10-10T20:00:00+02:00"),
        window_end=datetime.fromisoformat("2026-10-10T22:00:00+02:00"),
        ingested_at=INGESTED,
    )
    assert report == replay([eea])


def test_calendar_input_is_unchanged_with_fold_one():
    from scripts.observation_time import bucharest_utc_candidates

    label = datetime.fromisoformat("2025-10-26T03:30:00").replace(fold=1)
    result = bucharest_utc_candidates(label)
    assert label.fold == 1
    assert len(result) == 2
    assert result[1] - result[0] == timedelta(hours=1)


def test_missing_value_is_not_treated_as_an_explicit_null_invalidation(eea):
    del eea["value"]
    eea["validity"] = -1
    with pytest.raises(TypeError, match="finite number"):
        replay([eea])


def test_accepted_eea_cannot_have_a_null_value(eea):
    with pytest.raises(TypeError, match="finite number"):
        replay([dict(eea, value=None)])
