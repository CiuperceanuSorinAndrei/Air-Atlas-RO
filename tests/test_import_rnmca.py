import pytest

from scripts.import_rnmca import rnmca_effective_value


@pytest.fixture
def captured_data_element():
    return {
        "timestamp": 1791658800000,
        "usable": True,
        "dataElementFields": {
            "type": "Webs_FieldsHourlyValue",
            "value": 48.81861538888885,
            "status": 0,
            "version": 3613450,
            "percentage": 100.0,
            "standardDeviation": 16.38923614344896,
            "minTimestamp": 1791658690000,
            "minValue": 19.03124,
            "maxTimestamp": 1791656640000,
            "maxValue": 92.10496,
            "qaqcStateId": 78002,
            "qaqcStatusId": 79001,
        },
    }


def test_effective_value_reads_captured_value_without_override(captured_data_element):
    result = rnmca_effective_value(captured_data_element)

    assert result == 48.81861538888885


@pytest.mark.parametrize(
    "override", [12.5, 0, None], ids=["numeric", "zero", "unavailable"]
)
def test_effective_value_honors_range_override(captured_data_element, override):
    captured_data_element["rangeExceedance"] = {
        "measurementRangeId": 123,
        "rangeExceeded": "MIN",
        "overrideValue": override,
    }

    result = rnmca_effective_value(captured_data_element)

    if override is None:
        assert result is None
    else:
        assert result == override


@pytest.mark.parametrize("use_override", [False, True], ids=["ordinary", "override"])
@pytest.mark.parametrize("value", ["48.8", True, False], ids=["text", "true", "false"])
def test_effective_value_rejects_non_numeric_selected_value(
    captured_data_element, use_override, value
):
    if use_override:
        captured_data_element["rangeExceedance"] = {"overrideValue": value}
    else:
        captured_data_element["dataElementFields"]["value"] = value

    with pytest.raises(TypeError):
        rnmca_effective_value(captured_data_element)


@pytest.mark.parametrize("use_override", [False, True], ids=["ordinary", "override"])
@pytest.mark.parametrize("value", [0, 12.5, None], ids=["zero", "float", "unavailable"])
def test_effective_value_preserves_numeric_or_unavailable_selected_value(
    captured_data_element, use_override, value
):
    if use_override:
        captured_data_element["rangeExceedance"] = {"overrideValue": value}
    else:
        captured_data_element["dataElementFields"]["value"] = value

    result = rnmca_effective_value(captured_data_element)

    if value is None:
        assert result is None
    else:
        assert result == value
        assert type(result) in (int, float)


@pytest.mark.parametrize("use_override", [False, True], ids=["ordinary", "override"])
@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_effective_value_rejects_non_finite_selected_value(
    captured_data_element, use_override, value
):
    if use_override:
        captured_data_element["rangeExceedance"] = {"overrideValue": value}
    else:
        captured_data_element["dataElementFields"]["value"] = value

    with pytest.raises(ValueError):
        rnmca_effective_value(captured_data_element)


def test_effective_value_rejects_override_object_without_override_value(
    captured_data_element,
):
    captured_data_element["rangeExceedance"] = {"rangeExceeded": "MIN"}

    with pytest.raises(ValueError, match="Missing RNMCA overrideValue"):
        rnmca_effective_value(captured_data_element)
