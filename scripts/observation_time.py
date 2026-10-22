from datetime import UTC, datetime, timedelta


def rnmca_hourly_interval(timestamp_ms: int):
    if type(timestamp_ms) is not int:
        raise TypeError("timestamp_ms must be an integer")
    try:
        observed_to = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            milliseconds=timestamp_ms
        )
        observed_from = observed_to - timedelta(hours=1)
    except OverflowError as error:
        raise ValueError(
            "timestamp_ms cannot represent a complete hourly interval"
        ) from error
    return (observed_from, observed_to)
