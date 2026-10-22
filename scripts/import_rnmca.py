from copy import deepcopy
from datetime import UTC, datetime
from math import isfinite

from scripts.observation_time import rnmca_hourly_interval

RNMCA_QA_STATES = {78001: "raw", 78002: "validated", 78003: "certified"}
RNMCA_QA_STATUSES = frozenset({79001, 79002, 79003})
RNMCA_FIELD_TYPES = frozenset(
    {
        "Webs_FieldsHourlyValue",
        "Webs_FieldsDailyMobileValue",
        "Webs_FieldsDailyValue",
        "Webs_FieldsMinuteValue",
        "Webs_FieldsMobileValue",
        "Webs_FieldsSecondValue",
        "Webs_FieldsYearlyAccumulatedThresholdValue",
        "Webs_FieldsYearlyMobileValue",
        "Webs_FieldsYearlyValue",
    }
)
RNMCA_KNOWN_STATUS_MASK = (1 << 27) - 1
RNMCA_INVALID_STATUS_MASK = sum(
    1 << position for position in (1, 2, 3, 4, 5, 17, 19, 20, 21, 22, 23, 24, 26)
)


def _rnmca_fields(data_element):
    if not isinstance(data_element, dict):
        raise TypeError("RNMCA data element must be an object")
    fields = data_element.get("dataElementFields")
    if not isinstance(fields, dict):
        raise TypeError("RNMCA dataElementFields must be an object")
    return fields


def rnmca_effective_value(data_element):
    fields = _rnmca_fields(data_element)
    if "rangeExceedance" in data_element:
        override = data_element["rangeExceedance"]
        if not isinstance(override, dict):
            raise TypeError("RNMCA rangeExceedance must be an object")
        if "overrideValue" not in override:
            raise ValueError("Missing RNMCA overrideValue")
        effective_value = override["overrideValue"]
    else:
        if "value" not in fields:
            raise ValueError("Missing RNMCA value")
        effective_value = fields["value"]
    if effective_value is None:
        return None
    if type(effective_value) not in (int, float):
        raise TypeError("RNMCA value must be a number or None")
    if type(effective_value) is float and not isfinite(effective_value):
        raise ValueError("RNMCA value must be finite")
    return effective_value


def rnmca_hourly_is_eligible(data_element):
    fields = _rnmca_fields(data_element)
    field_type = fields.get("type")
    if not isinstance(field_type, str):
        raise TypeError("RNMCA field type must be text")
    if field_type not in RNMCA_FIELD_TYPES:
        raise ValueError("Unknown RNMCA field type")
    if field_type != "Webs_FieldsHourlyValue":
        return False
    if type(data_element.get("usable")) is not bool:
        raise TypeError("RNMCA usable must be a boolean")
    for key, known in (
        ("qaqcStateId", RNMCA_QA_STATES),
        ("qaqcStatusId", RNMCA_QA_STATUSES),
    ):
        if type(fields.get(key)) is not int:
            raise TypeError(f"RNMCA {key} must be an integer")
        if fields[key] not in known:
            raise ValueError(f"Unknown RNMCA {key}")
    status = fields.get("status")
    if type(status) is not int:
        raise TypeError("RNMCA status must be an integer")
    if status < 0 or status & ~RNMCA_KNOWN_STATUS_MASK:
        raise ValueError("Unknown RNMCA status flags")
    if (
        not data_element["usable"]
        or fields["qaqcStatusId"] != 79001
        or status & RNMCA_INVALID_STATUS_MASK
    ):
        return False
    return rnmca_effective_value(data_element) is not None


def normalize_rnmca_observation(
    data_element, *, location_id, measurement_value_id, ingested_at
):
    if type(location_id) is not int or type(measurement_value_id) is not int:
        raise TypeError("RNMCA location and measurement value IDs must be integers")
    if (location_id, measurement_value_id) != (27064, 6404090401):
        raise ValueError("RNMCA pilot supports only the captured DJ-1 / NO2 channel")
    if not isinstance(ingested_at, datetime):
        raise TypeError("RNMCA ingested_at must be a datetime")
    if ingested_at.utcoffset() is None:
        raise ValueError("RNMCA ingested_at must include its UTC offset")
    ingested_at = ingested_at.astimezone(UTC)
    if not rnmca_hourly_is_eligible(data_element):
        return None
    fields = data_element["dataElementFields"]
    version = fields.get("version")
    if type(version) is not int or version < 0:
        raise ValueError("RNMCA version must be a nonnegative integer")
    observed_from, observed_to = rnmca_hourly_interval(data_element.get("timestamp"))
    if observed_to > ingested_at:
        raise ValueError("RNMCA pilot requires completed hourly intervals")
    for key in ("minTimestamp", "maxTimestamp"):
        timestamp = fields.get(key)
        if timestamp is None:
            continue
        if type(timestamp) is not int:
            raise TypeError(f"RNMCA {key} must be an integer or None")
        if (
            not data_element["timestamp"] - 3_600_000
            <= timestamp
            <= data_element["timestamp"]
        ):
            raise ValueError(f"RNMCA {key} is outside the observation interval")
    return {
        "source": "RNMCA",
        "stationCode": "DJ-1",
        "sourceLocationId": location_id,
        "sourceMeasurementValueId": measurement_value_id,
        "sourceVersion": version,
        "pollutant": "NO2",
        "unit": "ug.m-3",
        "aggregationType": "hour",
        "value": rnmca_effective_value(data_element),
        "observedFrom": observed_from.isoformat(),
        "observedTo": observed_to.isoformat(),
        "reportedAt": None,
        "ingestedAt": ingested_at.isoformat(),
        "qaState": RNMCA_QA_STATES[fields["qaqcStateId"]],
        "qaqcStateId": fields["qaqcStateId"],
        "qaqcStatusId": fields["qaqcStatusId"],
        "sourceStatus": fields["status"],
        "normalizationRule": "rnmca-dj1-no2-hourly-epoch-utc-v1",
        "temporalStatus": "resolved",
        "rawObservation": deepcopy(data_element),
    }
