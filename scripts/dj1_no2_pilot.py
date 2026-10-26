import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from math import isclose

from scripts.import_eea import parse_timestamp, validate_observation
from scripts.import_rnmca import (
    _rnmca_ingested_at,
    _utc_hour,
    missing_rnmca_hourly_intervals,
    normalize_rnmca_observations,
    reconcile_rnmca_data_elements,
)
from scripts.observation_time import bucharest_utc_candidates

EEA_PILOT_RULE = "eea-dj1-no2-national-clock-bucharest-v1"
PILOT_START = datetime(2025, 1, 1, tzinfo=UTC)
PILOT_END = datetime(2026, 10, 12, 8, tzinfo=UTC)
PILOT_SERIES = "RO/SPO-RO0078A_00008_100"
HOUR = timedelta(hours=1)


def _validate_eea_input(row):
    if not isinstance(row, dict):
        raise TypeError("EEA pilot observation must be an object")
    json.dumps(row, sort_keys=True, allow_nan=False)
    if (
        row.get("source") != "EEA"
        or row.get("stationId") != "RO0078A"
        or row.get("samplingPointId") != PILOT_SERIES
        or row.get("pollutant") != "NO2"
        or row.get("unit") != "ug.m-3"
        or row.get("aggregationType") != "hour"
    ):
        raise ValueError("EEA pilot requires the captured DJ-1/NO2 hourly series")
    validity = row.get("validity")
    if type(validity) is not int or validity not in (-1, 1, 2, 3, 4):
        raise ValueError("Unknown EEA pilot validity")
    checked = dict(row, validity=1) if validity == -1 else row
    if validity == -1 and "value" in row and row["value"] is None:
        checked["value"] = 0
    validate_observation(checked)
    start = parse_timestamp(row["observedFrom"])
    end = parse_timestamp(row["observedTo"])
    if start.utcoffset() != HOUR or end.utcoffset() != HOUR:
        raise ValueError("EEA pilot requires original fixed-UTC+1 interval labels")
    _utc_hour(start, "EEA observedFrom")
    _utc_hour(end, "EEA observedTo")
    return start, end


def _eea_current_revisions(rows, ingested_at):
    revisions = {}
    latest = {}
    for row in rows:
        start, end = _validate_eea_input(row)
        if parse_timestamp(row["ingestedAt"]) > ingested_at:
            raise ValueError("EEA ingestion is after pilot replay ingestion")
        reported = parse_timestamp(row["reportedAt"]).astimezone(UTC)
        identity = (start, end)
        key = (identity, reported)
        content = json.dumps(
            {key: value for key, value in row.items() if key != "ingestedAt"},
            sort_keys=True,
            allow_nan=False,
        )
        if key in revisions and revisions[key] != content:
            raise ValueError(
                "Conflicting EEA data for the same interval and report time"
            )
        revisions[key] = content
        order = (reported, parse_timestamp(row["ingestedAt"]).astimezone(UTC))
        if identity not in latest or order > latest[identity][0]:
            latest[identity] = (order, row)
    return [deepcopy(latest[key][1]) for key in sorted(latest)]


def _normalize_eea_observation(row, reference, window_start, window_end):
    _, end = _validate_eea_input(row)
    local_end = end.astimezone(timezone(timedelta(hours=2))).replace(tzinfo=None)
    ends = bucharest_utc_candidates(local_end)
    result = deepcopy(row)
    result.update(
        stationCode="DJ-1",
        observedFrom=None,
        observedTo=None,
        normalizationRule=EEA_PILOT_RULE,
        rawObservation=deepcopy(row),
        temporalStatus="unresolved",
        candidateIntervals=[
            {"observedFrom": (end - HOUR).isoformat(), "observedTo": end.isoformat()}
            for end in ends
        ],
    )
    if not ends:
        result["temporalStatus"] = "nonexistent"
        return result
    if not any(window_start < end <= window_end for end in ends):
        raise ValueError("EEA observation is outside the requested window")
    if row["validity"] == -1:
        result["temporalStatus"] = "qa-rejected"
        return result
    if len(ends) > 1:
        result["temporalStatus"] = "ambiguous"
        return result
    declared_end = end.astimezone(UTC)
    if (
        declared_end not in ends
        and declared_end in reference
        and isclose(
            row["value"], reference[declared_end]["value"], rel_tol=0, abs_tol=1e-5
        )
    ):
        result["temporalStatus"] = "conflict"
        return result
    selected_end = ends[0]
    if selected_end in reference and not isclose(
        row["value"], reference[selected_end]["value"], rel_tol=0, abs_tol=1e-5
    ):
        result["temporalStatus"] = "conflict"
        return result
    if not window_start < selected_end <= window_end:
        raise ValueError("Resolved EEA observation is outside the requested window")
    if selected_end > parse_timestamp(row["ingestedAt"]).astimezone(UTC):
        raise ValueError("EEA pilot requires a completed observation interval")
    result.update(
        observedFrom=(selected_end - HOUR).isoformat(),
        observedTo=selected_end.isoformat(),
        temporalStatus="resolved",
    )
    return result


def reconcile_dj1_no2_window(
    rnmca_data_elements, eea_observations, *, window_start, window_end, ingested_at
):
    ingested_at = _rnmca_ingested_at(27064, 6404090401, ingested_at)
    window_start = _utc_hour(window_start, "window_start")
    window_end = _utc_hour(window_end, "window_end")
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")
    if window_end > ingested_at:
        raise ValueError("window_end must not be after ingestion")
    if window_start < PILOT_START or window_end > PILOT_END:
        raise ValueError(
            "Requested window is outside the investigated DJ-1/NO2 pilot period"
        )
    current = reconcile_rnmca_data_elements(
        rnmca_data_elements,
        location_id=27064,
        measurement_value_id=6404090401,
        ingested_at=ingested_at,
    )
    for element in current:
        end = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            milliseconds=element["timestamp"]
        )
        if not window_start < end <= window_end:
            raise ValueError("RNMCA raw revision is outside the requested window")
    accepted = normalize_rnmca_observations(
        current,
        location_id=27064,
        measurement_value_id=6404090401,
        ingested_at=ingested_at,
    )
    reference = {parse_timestamp(row["observedTo"]): row for row in accepted}
    selected = dict(reference)
    excluded = []
    normalized = []
    for row in _eea_current_revisions(eea_observations, ingested_at):
        observation = _normalize_eea_observation(
            row, reference, window_start, window_end
        )
        normalized.append(observation)
        if observation["temporalStatus"] != "resolved":
            excluded.append(observation)
            continue
        end = parse_timestamp(observation["observedTo"])
        if end not in selected:
            selected[end] = observation
        elif selected[end]["source"] == "EEA" and selected[end] != observation:
            raise ValueError(
                "Conflicting EEA observations normalize to the same UTC hour"
            )
    observations = [deepcopy(selected[end]) for end in sorted(selected)]
    missing = missing_rnmca_hourly_intervals(
        observations, window_start=window_start, window_end=window_end
    )
    return {
        "windowStart": window_start.isoformat(),
        "windowEnd": window_end.isoformat(),
        "normalizationRule": EEA_PILOT_RULE,
        "currentRnmcaRevisions": current,
        "eeaObservations": normalized,
        "excludedEeaObservations": excluded,
        "observations": observations,
        "missingIntervals": missing,
        "latestObservation": deepcopy(observations[-1]) if observations else None,
    }
