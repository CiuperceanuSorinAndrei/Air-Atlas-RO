from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


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


def bucharest_utc_candidates(local_time: datetime) -> tuple[datetime, ...]:
    if type(local_time) is not datetime:
        raise TypeError("local_time must be a datetime")
    if local_time.tzinfo is not None:
        raise ValueError("local_time must be timezone-naive")
    bucharest_zone = ZoneInfo("Europe/Bucharest")
    candidates = set()
    try:
        for fold in (0, 1):
            local_candidate = local_time.replace(tzinfo=bucharest_zone, fold=fold)
            utc_candidate = local_candidate.astimezone(UTC)
            roundtrip_local = utc_candidate.astimezone(bucharest_zone)
            roundtrip_label = roundtrip_local.replace(tzinfo=None)
            if roundtrip_label == local_time:
                candidates.add(utc_candidate)
    except OverflowError as error:
        raise ValueError("local_time cannot represent its UTC candidates") from error
    return tuple(sorted(candidates))
